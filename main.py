from nornir import InitNornir
from nornir_netmiko.tasks import netmiko_send_config, netmiko_send_command
from nornir.core.inventory import ConnectionOptions
import time
import getpass
import difflib
import os
import json
import argparse
from datetime import datetime

def condense_interface_ranges(interfaces):
    """Convert list of interfaces to condensed ranges (e.g., Ethernet1/1-5, Ethernet1/7-10)"""
    if not interfaces:
        return []
    
    # Extract interface numbers and sort them
    interface_nums = []
    for interface in interfaces:
        if interface.startswith('Ethernet1/'):
            try:
                num = int(interface.split('/')[-1])
                interface_nums.append(num)
            except ValueError:
                continue
    
    interface_nums.sort()
    
    if not interface_nums:
        return interfaces  # Return original if we can't parse
    
    # Group consecutive numbers into ranges
    ranges = []
    start = interface_nums[0]
    end = start
    
    for i in range(1, len(interface_nums)):
        if interface_nums[i] == end + 1:
            end = interface_nums[i]
        else:
            # End of consecutive sequence, add range
            if start == end:
                ranges.append(f"Ethernet1/{start}")
            else:
                ranges.append(f"Ethernet1/{start}-{end}")
            start = interface_nums[i]
            end = start
    
    # Add the last range
    if start == end:
        ranges.append(f"Ethernet1/{start}")
    else:
        ranges.append(f"Ethernet1/{start}-{end}")
    
    return ranges

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
    """Validate port-profile inheritance and detect L3 interfaces to skip"""
    try:
        # Get interface configuration to check port-profile inheritance
        interface_config = task.run(task=netmiko_send_command, command_string="show running-config interface")
        
        validation_results = {
            'port_profile_applied': 0,
            'port_profile_missing': [],
            'port_profile_already_applied': [],  # Track interfaces that already have port-profile
            'port_profile_failed': [],  # Track which interfaces failed to get port-profile
            'l3_interfaces_skipped': [],  # Track L3/router interfaces that are skipped
            'validation_passed': True
        }
        
        # Check port-profile inheritance and L3 configuration in config
        config_lines = interface_config.result.split('\n')
        current_interface = None
        interfaces_with_profile = set()
        interfaces_with_baremetal = set()
        l3_interfaces = set()  # Interfaces with L3 configuration
        
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
            elif current_interface and (
                line.startswith('ip address') or 
                line.startswith('ipv6 address') or
                line.startswith('no switchport') or
                'routed' in line.lower()
            ):
                # Detect L3/routed interfaces by IP address configuration or no switchport
                l3_interfaces.add(current_interface)
            elif line.startswith('inherit port-profile') and current_interface:
                # Track any port-profile inheritance (BAREMETAL, BLOCKER, etc.)
                interfaces_with_profile.add(current_interface)
                # Specifically track BAREMETAL port-profile
                if 'inherit port-profile BAREMETAL' in line:
                    interfaces_with_baremetal.add(current_interface)
        
        # Check which target interfaces have BAREMETAL port-profile applied vs need it
        for i in range(1, 47):
            interface = f"Ethernet1/{i}"
            if interface in l3_interfaces:
                # Skip L3/routed interfaces - they should not have port-profiles
                validation_results['l3_interfaces_skipped'].append(interface)
                validation_results['port_profile_applied'] += 1  # Count as "handled" 
            elif interface in interfaces_with_baremetal:
                # Interface already has BAREMETAL port-profile
                validation_results['port_profile_applied'] += 1
                validation_results['port_profile_already_applied'].append(interface)
            elif interface in interfaces_with_profile:
                # Interface has a different port-profile (like BLOCKER) - should be skipped
                validation_results['port_profile_applied'] += 1
                validation_results['port_profile_already_applied'].append(interface)
            else:
                # Interface needs BAREMETAL port-profile
                validation_results['port_profile_missing'].append(interface)
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

def configure_interfaces(task, missing_interfaces=None):
    """Configure only the interfaces that need port-profile applied"""
    if not missing_interfaces:
        # Fallback to all interfaces if no specific list provided
        missing_interfaces = [f"Ethernet1/{i}" for i in range(1, 47)]
    
    cmds = []
    for interface in missing_interfaces:
        cmds.append(f"interface {interface}")
        cmds.append("inherit port-profile BAREMETAL")
        cmds.append("exit")
    task.run(task=netmiko_send_config, config_commands=cmds)

def create_condensed_diff(before_config, after_config, hostname):
    """Create a condensed summary of configuration changes"""
    # Filter out comment lines starting with !
    before_filtered = filter_config_lines(before_config)
    after_filtered = filter_config_lines(after_config)
    
    # If filtered configs are identical, no meaningful changes
    if before_filtered == after_filtered:
        return None
    
    before_lines = before_filtered.splitlines()
    after_lines = after_filtered.splitlines()
    
    # Create the full diff for analysis
    diff = list(difflib.unified_diff(
        before_lines, 
        after_lines, 
        fromfile=f"{hostname}_before.cfg",
        tofile=f"{hostname}_after.cfg",
        lineterm=""
    ))
    
    # Analyze the diff to create a condensed summary
    added_port_profiles = []
    added_interfaces = []
    other_changes = []
    
    current_interface = None
    for line in diff:
        if line.startswith('+') and not line.startswith('+++'):
            content = line[1:].strip()
            if content.startswith('port-profile type ethernet BAREMETAL'):
                added_port_profiles.append("BAREMETAL port-profile")
            elif content.startswith('interface Ethernet1/'):
                current_interface = content.split()[1]
            elif content.startswith('inherit port-profile BAREMETAL') and current_interface:
                added_interfaces.append(current_interface)
            elif content and not content.startswith('interface') and 'port-profile' not in content:
                other_changes.append(content)
    
    # Create condensed summary - minimal and focused
    summary_lines = []
    
    if added_interfaces:
        # Condense interface ranges
        condensed_interfaces = condense_interface_ranges(added_interfaces)
        summary_lines.append(f"[{hostname}] ✓ Configured {len(added_interfaces)} ports: {', '.join(condensed_interfaces)}")
    
    if not summary_lines:
        return None  # No meaningful changes to summarize
    
    return '\n'.join(summary_lines)

def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Configure port profiles on network devices')
    parser.add_argument('--dry-run', action='store_true', 
                       help='Perform validation and show what would be configured without making changes')
    args = parser.parse_args()
    
    dry_run = args.dry_run
    
    if dry_run:
        print("="*60)
        print("DRY RUN MODE - No actual changes will be made")
        print("="*60)
    
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
            already_applied = validation_data.get('port_profile_already_applied', [])
            l3_skipped = validation_data.get('l3_interfaces_skipped', [])
            
            # Enhanced summary with details
            if missing_count == 0:
                print(f"[{hostname}] ✓ All 46 port-profiles already applied")
            else:
                print(f"[{hostname}] {applied_count}/46 applied, {missing_count} missing")
            
            # Show L3/router interfaces that are automatically skipped
            if l3_skipped:
                print(f"    L3/Router interfaces (auto-skipped): {len(l3_skipped)} interfaces")
                # Show condensed interface ranges
                condensed_l3 = condense_interface_ranges(l3_skipped)
                for range_str in condensed_l3:
                    print(f"      {range_str} (has IP/routed config)")
            
            # Show interfaces that already have port-profile (will be ignored)
            if already_applied:
                print(f"    Already configured (will be skipped): {len(already_applied)} interfaces")
                # Show condensed interface ranges
                condensed_applied = condense_interface_ranges(already_applied)
                for range_str in condensed_applied:
                    print(f"      {range_str}")
            
            # Show interfaces that need configuration
            missing_interfaces = validation_data.get('port_profile_missing', [])
            if missing_interfaces:
                print(f"    Need configuration: {len(missing_interfaces)} interfaces")
                # Show condensed interface ranges
                condensed_missing = condense_interface_ranges(missing_interfaces)
                for range_str in condensed_missing:
                    print(f"      {range_str}")
                
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
    
    # Get MAC address tables before changes (only if not dry run to save time)
    if not dry_run:
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
    else:
        print(f"\nInitial configuration capture completed in {elapsed:.2f} seconds.")
    print("="*60)

    if dry_run:
        print("DRY RUN: Would configure port-profile with commands:")
        print("  - port-profile type ethernet BAREMETAL")
        print("  - mtu 9000")
        print("  - no snmp trap link-status")
        print("  - spanning-tree port type edge trunk")
        print("  - state enabled")
        print("  - exit")
        print("\nDRY RUN: Would apply port-profile to interfaces Ethernet1/1 through Ethernet1/46")
        print("="*60)
        print("DRY RUN CONFIGURATION PLAN:")
        print("="*60)
        
        # Show detailed plan based on pre-validation
        for hostname, validation_data in pre_validation_data.items():
            missing_interfaces = validation_data.get('port_profile_missing', [])
            already_applied = validation_data.get('port_profile_already_applied', [])
            l3_skipped = validation_data.get('l3_interfaces_skipped', [])
            
            print(f"\n[{hostname}] Configuration Plan:")
            
            # Show L3 interfaces that would be skipped
            if l3_skipped:
                print(f"  SKIP: {len(l3_skipped)} L3/router interfaces (auto-detected)")
                condensed_l3 = condense_interface_ranges(l3_skipped)
                for range_str in condensed_l3:
                    print(f"    {range_str} (has IP/routed config)")
            
            # Show interfaces that would be skipped
            if already_applied:
                print(f"  SKIP: {len(already_applied)} interfaces already configured")
                condensed_applied = condense_interface_ranges(already_applied)
                for range_str in condensed_applied:
                    print(f"    {range_str}")
            
            # Show interfaces that would be configured
            if missing_interfaces:
                print(f"  CONFIGURE: {len(missing_interfaces)} interfaces need port-profile")
                condensed_missing = condense_interface_ranges(missing_interfaces)
                for range_str in condensed_missing:
                    print(f"    {range_str}")
            else:
                print(f"  ✓ No configuration needed - all port-profiles already applied")
        
        print("\n" + "="*60)
        print("DRY RUN COMPLETED - No actual changes were made")
        print("="*60)
        return
    
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
    
    # Create a custom task that passes missing interfaces to each host
    def configure_interfaces_for_host(task):
        hostname = str(task.host)
        if hostname in pre_validation_data:
            missing_interfaces = pre_validation_data[hostname].get('port_profile_missing', [])
            if missing_interfaces:
                print(f"[{hostname}] Configuring {len(missing_interfaces)} interfaces")
                configure_interfaces(task, missing_interfaces)
            else:
                print(f"[{hostname}] No interfaces need configuration - skipping")
        else:
            # Fallback to original behavior if no pre-validation data
            configure_interfaces(task)
    
    result2 = nr.run(task=configure_interfaces_for_host)
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
            
            missing_count = len(validation_data.get('port_profile_missing', []))
            applied_count = validation_data.get('port_profile_applied', 0)
            already_applied = validation_data.get('port_profile_already_applied', [])
            l3_skipped = validation_data.get('l3_interfaces_skipped', [])
            
            # Enhanced summary with details (matching pre-validation format)
            if missing_count == 0:
                print(f"[{hostname}] ✓ All 46 port-profiles successfully applied")
            else:
                print(f"[{hostname}] {applied_count}/46 applied, {missing_count} still missing")
            
            # Show L3/router interfaces that were automatically skipped
            if l3_skipped:
                print(f"    L3/Router interfaces (auto-skipped): {len(l3_skipped)} interfaces")
                # Show condensed interface ranges
                condensed_l3 = condense_interface_ranges(l3_skipped)
                for range_str in condensed_l3:
                    print(f"      {range_str} (has IP/routed config)")
            
            # Show interfaces that now have port-profile applied (includes both newly configured and previously configured)
            if already_applied:
                print(f"    Port-profile applied: {len(already_applied)} interfaces")
                # Show condensed interface ranges
                condensed_applied = condense_interface_ranges(already_applied)
                for range_str in condensed_applied:
                    print(f"      {range_str}")
                
                # Show breakdown of what was newly configured vs already configured
                if hostname in pre_validation_data:
                    pre_already_applied = set(pre_validation_data[hostname].get('port_profile_already_applied', []))
                    post_already_applied = set(already_applied)
                    newly_configured = post_already_applied - pre_already_applied
                    was_already_configured = pre_already_applied.intersection(post_already_applied)
                    
                    if newly_configured:
                        print(f"      → Newly configured: {len(newly_configured)} interfaces")
                        condensed_new = condense_interface_ranges(list(newly_configured))
                        for range_str in condensed_new:
                            print(f"        {range_str}")
                    
                    if was_already_configured:
                        print(f"      → Another port-profile currently active: {len(was_already_configured)} interfaces")
                        condensed_prev = condense_interface_ranges(list(was_already_configured))
                        for range_str in condensed_prev:
                            print(f"        {range_str}")
            
            # Show interfaces that still need configuration (if any)
            missing_interfaces = validation_data.get('port_profile_missing', [])
            if missing_interfaces:
                print(f"    Still missing configuration: {len(missing_interfaces)} interfaces")
                # Show condensed interface ranges
                condensed_missing = condense_interface_ranges(missing_interfaces)
                for range_str in condensed_missing:
                    print(f"      {range_str}")
            
            # Show configuration success rate
            if hostname in pre_validation_data:
                failure_analysis = analyze_config_failures(pre_validation_data[hostname], validation_data, hostname)
                if failure_analysis:
                    success_rate = failure_analysis['configuration_success_rate']
                    print(f"    Configuration success rate: {success_rate}")
                
            if validation_data.get('error'):
                print(f"    - Error: {validation_data['error']}")
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
            condensed_diff = create_condensed_diff(before_configs[hostname], after_config, hostname)
            
            if diff_content:
                # Only create directories when we have actual diffs
                if not diff_created:
                    os.makedirs(main_diff_dir, exist_ok=True)
                    diff_created = True
                
                device_dir = os.path.join(main_diff_dir, hostname)
                os.makedirs(device_dir, exist_ok=True)
                
                # Save detailed diff
                diff_filename = os.path.join(device_dir, f"config_diff_detailed_{timestamp}.txt")
                with open(diff_filename, 'w') as f:
                    f.write(diff_content)
                print(f"[{hostname}] Detailed configuration diff saved to {diff_filename}")
                
                # Display brief summary in console (only if meaningful)
                if condensed_diff:
                    print(f"{condensed_diff}")
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
