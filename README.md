# Cisco Nexus Port-Profile Automation with Nornir

A Python-based network automation script using Nornir to configure port-profiles on Cisco Nexus switches and apply them to multiple interfaces. The script provides comprehensive validation, change tracking, and diff generation.


## Requirements

Install the required Python packages:

```bash
pip install -r requirements.txt
```

1. **Configure your inventory**: Update `hosts.yaml` with your Nexus switch IP addresses
2. **Run the script**:
   ```bash
   python main.py
   ```
3. **Enter credentials** when prompted:
   ```
   SSH Username: 
   SSH Password: 
   ```

## What the Script Does

### 1. Pre-Change Validation
- Connects to each device
- Checks current port-profile configuration
- Reports missing configurations

### 2. Configuration Changes
- Creates "BAREMETAL" port-profile with:
  - MTU 9000
  - No SNMP trap link-status
  - Spanning-tree port type edge trunk
  - State enabled
- Applies port-profile to interfaces Ethernet1/1-46

### 3. Post-Change Validation
- Verifies port-profile inheritance
- Reports configuration success rate
- Shows failed interfaces (if any)

### 4. Change Tracking
- Captures running configuration before/after
- Generates configuration diffs
- Captures MAC address tables
- Saves all diffs to timestamped files
