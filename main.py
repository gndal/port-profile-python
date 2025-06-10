from nornir import InitNornir
from nornir_netmiko.tasks import netmiko_send_config, netmiko_send_command
from nornir.core.inventory import ConnectionOptions
import time
import getpass
import difflib
import os
import json
from datetime import datetime

def get_running_config(task):
    """Get running configuration from device"""
    result = task.run(task=netmiko_send_command, command_string="show running-config")
    return result.result

def get_mac_table(task):
    """Get MAC address table from device"""
    try:
        result = task.run(task=netmiko_send_command, command_string="show mac address-table")
        return result.result
    except Exception as e:
        print(f"Error getting MAC table for {task.host}: {e}")
        return None

def validate_interfaces(task):
    """Validate port-profile inheritance only"""
    try:
        # Get interface configuration to check port-profile inheritance
        interface_config = task.run(task=netmiko_send_command, command_string="show running-config interface")
        
        validation_results = {
            'port_profile_applied': 0,
            'port_profile_missing': [],
            'port_profile_failed': [],  # Track which interfaces failed to get port-profile
            'validation_passed': True
        }
        
        # Check port-profile inheritance in config
        config_lines = interface_config.result.split('\n')
        current_interface = None
        interfaces_with_profile = set()
        
        for line in config_lines:
            line = line.strip()
            if line.startswith('interface '):
                # Extract interface name
                interface_part = line.split()[1] if len(line.split()) > 1 else ''
                if 'ethernet1/' in interface_part.lower() or 'eth1/' in interface_part.lower():
                    # Normalize interface name
                    current_interface = interface_part.replace('Eth1/', 'Ethernet1/')
                    if current_interface.startswith('ethernet1/'):
                        current_interface = current_interface.replace('ethernet1/', 'Ethernet1/')
            elif 'inherit port-profile BAREMETAL' in line and current_interface:
                interfaces_with_profile.add(current_interface)
        
        # Check which target interfaces have port-profile applied
        for i in range(1, 47):
            interface = f"Ethernet1/{i}"
            if interface in interfaces_with_profile:
                validation_results['port_profile_applied'] += 1
            else:
                validation_results['port_profile_missing'].append(interface)
                # This is a configuration failure
                validation_results['port_profile_failed'].append(interface)
        
        # Overall validation status based only on port-profile configuration
        if validation_results['port_profile_missing']:
            validation_results['validation_passed'] = False
        
        return validation_results
        
    except Exception as e:
        return {
            'error': str(e),
            'validation_passed': False
        }

def analyze_config_failures(before_validation, after_validation, hostname):
    """Analyze which interfaces failed to get configuration applied"""
    if not before_validation or not after_validation:
        return None
    
    before_missing = set(before_validation.get('port_profile_missing', []))
    after_missing = set(after_validation.get('port_profile_missing', []))
    
    # Interfaces that should have been configured but still missing
    still_missing = before_missing.intersection(after_missing)
    
    # Interfaces that were successfully configured
    successfully_configured = before_missing - after_missing
    
    # New failures (shouldn't happen, but good to check)
    new_failures = after_missing - before_missing
    
    total_configured = len(successfully_configured)
    total_attempted = len(before_missing)
    
    if total_attempted > 0:
        success_percentage = (total_configured / total_attempted) * 100
    else:
        success_percentage = 100.0
    
    return {
        'hostname': hostname,
        'total_target_interfaces': 46,
        'before_missing_count': len(before_missing),
        'after_missing_count': len(after_missing),
        'successfully_configured': list(successfully_configured),
        'still_missing': list(still_missing),
        'new_failures': list(new_failures),
        'configuration_success_rate': f"{total_configured}/{total_attempted} ({success_percentage:.1f}%)"
    }

def save_validation_results(results, hostname, timestamp, suffix):
    """Save validation results to file"""
    validation_dir = "validations"
    os.makedirs(validation_dir, exist_ok=True)
    
    device_dir = os.path.join(validation_dir, hostname)
    os.makedirs(device_dir, exist_ok=True)
    
    filename = os.path.join(device_dir, f"validation_{suffix}_{timestamp}.json")
    
    with open(filename, 'w') as f:
        json.dump(results, f, indent=2)
    
    return filename

def filter_config_lines(config_text):
    """Filter out comment lines starting with ! for meaningful comparison"""
    if not config_text:
        return ""
    
    lines = config_text.split('\n')
    filtered_lines = []
    
    for line in lines:
        # Skip lines starting with ! (comments/timestamps)
        if not line.strip().startswith('!'):
            filtered_lines.append(line)
    
    return '\n'.join(filtered_lines)

def create_diff(before_config, after_config, hostname):
    """Create diff between before and after configurations, ignoring comment lines"""
    # Filter out comment lines starting with !
    before_filtered = filter_config_lines(before_config)
    after_filtered = filter_config_lines(after_config)
    
    # If filtered configs are identical, no meaningful changes
    if before_filtered == after_filtered:
        return None
    
    before_lines = before_filtered.splitlines(keepends=True)
    after_lines = after_filtered.splitlines(keepends=True)
    
    diff = list(difflib.unified_diff(
        before_lines, 
        after_lines, 
        fromfile=f"{hostname}_before.cfg",
        tofile=f"{hostname}_after.cfg",
        lineterm=""
    ))
    
    return ''.join(diff)

def create_table_diff(before_table, after_table, hostname, table_type):
    """Create diff between before and after tables (MAC)"""
    if not before_table or not after_table:
        return None
        
    before_lines = before_table.splitlines(keepends=True)
    after_lines = after_table.splitlines(keepends=True)
    
    diff = list(difflib.unified_diff(
        before_lines, 
        after_lines, 
        fromfile=f"{hostname}_{table_type}_before.txt",
        tofile=f"{hostname}_{table_type}_after.txt",
        lineterm=""
    ))
    
    return ''.join(diff)

def configure_port_profile(task):
    cmds = [
        "port-profile type ethernet BAREMETAL",
        "mtu 9000",
        "no snmp trap link-status",
        "spanning-tree port type edge trunk",
        "state enabled",
        "exit",
    ]
    task.run(task=netmiko_send_config, config_commands=cmds)

def configure_interfaces(task):
    cmds = []
    for i in range(1, 47):
        cmds.append(f"interface Ethernet1/{i}")
        cmds.append("inherit port-profile BAREMETAL")
        cmds.append("exit")
    task.run(task=netmiko_send_config, config_commands=cmds)

def main():
    nr = InitNornir(config_file="config.yaml")

    # Prompt for SSH credentials
    username = input("SSH Username: ")
    password = getpass.getpass("SSH Password: ")

    # Inject credentials and platform/device_type into each host
    for host in nr.inventory.hosts.values():
        host.username = username
        host.password = password
        host.platform = "nxos"
        host.connection_options["netmiko"] = ConnectionOptions(
            extras={"device_type": "cisco_nxos"}
        )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    print("="*60)
    print("Running pre-change validations...")
    
    # Pre-change validations - no JSON saving
    pre_validation_results = nr.run(task=validate_interfaces)
    pre_validation_data = {}
    
    for hostname, result in pre_validation_results.items():
        if not result.failed:
            validation_data = result.result
            pre_validation_data[hostname] = validation_data
            
            missing_count = len(validation_data.get('port_profile_missing', []))
            applied_count = validation_data.get('port_profile_applied', 0)
            
            # Simple summary only
            if missing_count == 0:
                print(f"[{hostname}] ✓ All 45 port-profiles already applied")
            else:
                print(f"[{hostname}] {applied_count}/45 applied, {missing_count} missing")
                
            if validation_data.get('error'):
                print(f"    - Error: {validation_data['error']}")
        else:
            print(f"[{hostname}] ✗ Validation failed: {result.exception}")

    print("="*60)
    print("Capturing initial configurations and tables...")
    
    # Get configuration before changes
    before_configs = {}
    before_mac_tables = {}
    
    start = time.time()
    before_result = nr.run(task=get_running_config)
    elapsed = time.time() - start
    
    for hostname, result in before_result.items():
        if not result.failed:
            before_configs[hostname] = result.result
            print(f"[{hostname}] Before config captured")
        else:
            print(f"[{hostname}] FAILED to get initial config: {result.exception}")
    
    # Get MAC address tables before changes
    print("Capturing MAC address tables...")
    start = time.time()
    mac_result = nr.run(task=get_mac_table)
    elapsed_mac = time.time() - start
    
    for hostname, result in mac_result.items():
        if not result.failed and result.result:
            before_mac_tables[hostname] = result.result
            print(f"[{hostname}] Before MAC table captured")
        else:
            print(f"[{hostname}] FAILED to get MAC table or no data")
    
    print(f"\nInitial data capture completed in {elapsed + elapsed_mac:.2f} seconds.")
    print("="*60)

    print("Configuring port-profile...")
    start = time.time()
    result1 = nr.run(task=configure_port_profile)
    elapsed = time.time() - start
    for host in result1.keys():
        if result1[host].failed:
            print(f"[{host}] FAILED: {result1[host].exception}")
        else:
            print(f"[{host}] DONE")
    print(f"\nPort-profile configuration completed in {elapsed:.2f} seconds.")
    print("="*60)

    print("Applying port-profile to interfaces...")
    start = time.time()
    result2 = nr.run(task=configure_interfaces)
    elapsed = time.time() - start
    for host in result2.keys():
        if result2[host].failed:
            print(f"[{host}] FAILED: {result2[host].exception}")
        else:
            print(f"[{host}] DONE")
    print(f"\nInterface configuration completed in {elapsed:.2f} seconds.")
    print("="*60)

    print("Running post-change validations...")
    
    # Post-change validations - no file saving
    post_validation_results = nr.run(task=validate_interfaces)
    
    for hostname, result in post_validation_results.items():
        if not result.failed:
            validation_data = result.result
            
            applied_count = validation_data.get('port_profile_applied', 0)
            missing_count = len(validation_data.get('port_profile_missing', []))
            
            # Simple summary
            if missing_count == 0:
                print(f"[{hostname}] ✓ All 45 port-profiles successfully applied")
            else:
                print(f"[{hostname}] {applied_count}/45 applied, {missing_count} still missing")
            
            # Show configuration results
            if hostname in pre_validation_data:
                failure_analysis = analyze_config_failures(pre_validation_data[hostname], validation_data, hostname)
                
                if failure_analysis:
                    success_rate = failure_analysis['configuration_success_rate']
                    print(f"    Configuration success: {success_rate}")
                    
                    if failure_analysis['still_missing']:
                        failed_interfaces = failure_analysis['still_missing'][:3]  # Show first 3
                        print(f"    Failed: {', '.join(failed_interfaces)}")
                        if len(failure_analysis['still_missing']) > 3:
                            print(f"    ... and {len(failure_analysis['still_missing']) - 3} more")
        else:
            print(f"[{hostname}] ✗ Post-validation failed: {result.exception}")

    print("="*60)
    print("Capturing final configurations and tables...")
    
    # Get configuration after changes
    start = time.time()
    after_result = nr.run(task=get_running_config)
    elapsed = time.time() - start
    
    # Get MAC address tables after changes
    print("Capturing final MAC address tables...")
    after_mac_result = nr.run(task=get_mac_table)
    
    # Create main diffs directory only if needed
    main_diff_dir = "diffs"
    diff_created = False
    
    # Process configuration diffs
    for hostname, result in after_result.items():
        if not result.failed and hostname in before_configs:
            after_config = result.result
            print(f"[{hostname}] After config captured")
            
            # Create and save config diff
            diff_content = create_diff(before_configs[hostname], after_config, hostname)
            
            if diff_content:
                # Only create directories when we have actual diffs
                if not diff_created:
                    os.makedirs(main_diff_dir, exist_ok=True)
                    diff_created = True
                
                device_dir = os.path.join(main_diff_dir, hostname)
                os.makedirs(device_dir, exist_ok=True)
                
                diff_filename = os.path.join(device_dir, f"config_diff_{timestamp}.txt")
                with open(diff_filename, 'w') as f:
                    f.write(diff_content)
                print(f"[{hostname}] Configuration diff saved to {diff_filename}")
                
                # Display summary of changes
                added_lines = len([line for line in diff_content.split('\n') if line.startswith('+')])
                removed_lines = len([line for line in diff_content.split('\n') if line.startswith('-')])
                print(f"[{hostname}] Config changes: +{added_lines} lines, -{removed_lines} lines")
            else:
                print(f"[{hostname}] No meaningful configuration changes detected")
        else:
            if result.failed:
                print(f"[{hostname}] FAILED to get final config: {result.exception}")
            else:
                print(f"[{hostname}] No initial config available for comparison")
    
    # Process MAC table diffs
    for hostname, result in after_mac_result.items():
        if not result.failed and result.result and hostname in before_mac_tables:
            after_mac = result.result
            print(f"[{hostname}] After MAC table captured")
            
            # Create MAC table diff
            mac_diff = create_table_diff(before_mac_tables[hostname], after_mac, hostname, "mac")
            if mac_diff:
                # Only create directories when we have actual MAC diffs
                if not diff_created:
                    os.makedirs(main_diff_dir, exist_ok=True)
                    diff_created = True
                
                device_dir = os.path.join(main_diff_dir, hostname)
                os.makedirs(device_dir, exist_ok=True)
                
                mac_diff_filename = os.path.join(device_dir, f"mac_diff_{timestamp}.txt")
                with open(mac_diff_filename, 'w') as f:
                    f.write(mac_diff)
                print(f"[{hostname}] MAC table diff saved to {mac_diff_filename}")
            else:
                print(f"[{hostname}] No MAC table changes detected")
    
    print(f"\nFinal data capture and diff creation completed.")
    print("="*60)
    print("Configuration changes completed!")

if __name__ == "__main__":
    main()