from nornir import InitNornir
from nornir_netmiko.tasks import netmiko_send_config
from nornir.core.inventory import ConnectionOptions
import time
import getpass

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
    print("Configuring port-profile...")
    start = time.time()
    result1 = nr.run(task=configure_port_profile)
    elapsed = time.time() - start
    for host in result1.keys():
        print(f"[{host}] DONE")
    print(f"\nPort-profile configuration completed in {elapsed:.2f} seconds.")
    print("="*60)

    print("Applying port-profile to interfaces...")
    start = time.time()
    result2 = nr.run(task=configure_interfaces)
    elapsed = time.time() - start
    for host in result2.keys():
        print(f"[{host}] DONE")
    print(f"\nInterface configuration completed in {elapsed:.2f} seconds.")
    print("="*60)

if __name__ == "__main__":
    main()