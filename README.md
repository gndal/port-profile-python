# Cisco Nexus Port-Profile Automation with Nornir

A Python-based network automation script using Nornir to configure port-profiles on Cisco Nexus switches and apply them to multiple interfaces. The script provides comprehensive validation, change tracking, and diff generation.


## Requirements

### Setting up a Virtual Environment (Recommended)

It's recommended to use a virtual environment to avoid conflicts with other Python packages:

```bash
# Create a virtual environment
python3 -m venv .venv

# Activate the virtual environment
# On macOS/Linux:
source .venv/bin/activate

# On Windows:
# .venv\Scripts\activate

# Your terminal prompt should now show (.venv) indicating the virtual environment is active
```

### Installing Dependencies

Install the required Python packages:

```bash
pip install -r requirements.txt
```

### Deactivating the Virtual Environment

When you're done working with the script, you can deactivate the virtual environment:

```bash
deactivate
```

## Setup and Usage

1. **Set up virtual environment** (see above)
2. **Configure your inventory**: Update `hosts.yaml` with your Nexus switch IP addresses
3. **Run the script**:
   ```bash
   python main.py
   ```
4. **Enter credentials** when prompted:
   ```
   SSH Username: 
   SSH Password: 
   ```

## What the Script Does

### 1. Pre-Change Validation
- Connects to each device
- Checks current port-profile configuration
- **Automatically detects and skips L3/router interfaces (those with IP addresses or 'no switchport')**
- **Lists interfaces that already have port-profiles applied (will be ignored) in condensed ranges**
- **Lists interfaces that need configuration in condensed ranges**
- Reports missing configurations

### 2. Configuration Changes
- Creates "BAREMETAL" port-profile with:
  - MTU 9000
  - No SNMP trap link-status
  - Spanning-tree port type edge trunk
  - State enabled
- Applies port-profile to interfaces Ethernet1/1-46 (excluding L3/router interfaces)
- **Automatically skips L3 interfaces with IP configuration**

### 3. Post-Change Validation
- Verifies port-profile inheritance
- Reports configuration success rate
- Shows failed interfaces (if any)

### 4. Change Tracking
- Captures running configuration before/after
- **Displays condensed configuration summaries in console**
- **Generates detailed configuration diffs**
- Captures MAC address tables
- **Only configures interfaces that need changes (skips already configured ones)**
- Saves diffs to timestamped files in `/diffs/[hostname]/` directory

#### Generated Files:
- `config_diff_detailed_[timestamp].txt` - Traditional unified diff format
- `mac_diff_[timestamp].txt` - MAC address table changes (if any)

#### Console Output:
- **Enhanced configuration summary with port ranges and individual port lists**
- Real-time progress and status updates

## Usage

### Normal Execution
```bash
python main.py
```

### Dry Run Mode
To preview what changes would be made without actually configuring the devices:

```bash
python main.py --dry-run
```

The dry run mode will:
- Perform pre-change validation
- **Automatically detect and list L3/router interfaces that will be skipped**
- **List interfaces that already have port-profiles applied (will be skipped) in condensed ranges**
- **List interfaces that need configuration in condensed ranges**
- Show which interfaces would be configured
- Display the exact commands that would be executed
- Skip actual configuration changes
- Skip MAC table captures to save time
- **Not make any changes to the devices**

## L3 Interface Detection

The script automatically detects and skips L3/router interfaces to prevent applying port-profiles to routed interfaces. An interface is considered L3 if it has:

- `ip address` configuration
- `ipv6 address` configuration  
- `no switchport` configuration
- Any configuration containing the word "routed"

These interfaces are automatically excluded from port-profile configuration and clearly marked as "L3/Router interfaces (auto-skipped)" in the output.
