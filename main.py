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
    for i in range(2, 47):
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

    print("Capturing final configurations and tables...")
    
    # Get configuration after changes
    start = time.time()
    after_result = nr.run(task=get_running_config)
    elapsed = time.time() - start
    
    # Get MAC address tables after changes
    print("Capturing final MAC address tables...")
    after_mac_result = nr.run(task=get_mac_table)
    
    # Create main diffs directory
    main_diff_dir = "diffs"
    os.makedirs(main_diff_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # Process configuration diffs
    for hostname, result in after_result.items():
        if not result.failed and hostname in before_configs:
            # Create device-specific directory
            device_dir = os.path.join(main_diff_dir, hostname)
            os.makedirs(device_dir, exist_ok=True)
            
            after_config = result.result
            print(f"[{hostname}] After config captured")
            
            # Create and save config diff
            diff_content = create_diff(before_configs[hostname], after_config, hostname)
            
            if diff_content:
                diff_filename = os.path.join(device_dir, f"config_diff_{timestamp}.txt")
                with open(diff_filename, 'w') as f:
                    f.write(diff_content)
                print(f"[{hostname}] Configuration diff saved to {diff_filename}")
                
                # Display summary of changes
                added_lines = len([line for line in diff_content.split('\n') if line.startswith('+')])
                removed_lines = len([line for line in diff_content.split('\n') if line.startswith('-')])
                print(f"[{hostname}] Config changes: +{added_lines} lines, -{removed_lines} lines")
            else:
                print(f"[{hostname}] No configuration changes detected")
        else:
            if result.failed:
                print(f"[{hostname}] FAILED to get final config: {result.exception}")
            else:
                print(f"[{hostname}] No initial config available for comparison")
    
    # Process MAC table diffs
    for hostname, result in after_mac_result.items():
        if not result.failed and result.result and hostname in before_mac_tables:
            # Create device-specific directory
            device_dir = os.path.join(main_diff_dir, hostname)
            os.makedirs(device_dir, exist_ok=True)
            
            after_mac = result.result
            print(f"[{hostname}] After MAC table captured")
            
            # Create MAC table diff
            mac_diff = create_table_diff(before_mac_tables[hostname], after_mac, hostname, "mac")
            if mac_diff:
                mac_diff_filename = os.path.join(device_dir, f"mac_diff_{timestamp}.txt")
                with open(mac_diff_filename, 'w') as f:
                    f.write(mac_diff)
                print(f"[{hostname}] MAC table diff saved to {mac_diff_filename}")
            else:
                print(f"[{hostname}] No MAC table changes detected")
    
    print(f"\nFinal data capture and diff creation completed.")
    print("="*60)
    print("Configuration changes completed!")
    print(f"Check the '{main_diff_dir}/' directory for device-specific folders:")
    print("Each device folder contains:")
    print("- config_diff_<timestamp>.txt (configuration changes, comment lines ignored)")
    print("- mac_diff_<timestamp>.txt (MAC address table changes)")

if __name__ == "__main__":
    main()