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
- **Lists interfaces that already have port-profiles applied (will be ignored) in condensed ranges**
- **Lists interfaces that need configuration in condensed ranges**
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
- **Generates condensed configuration summaries**
- **Generates detailed configuration diffs**
- Captures MAC address tables
- **Only configures interfaces that need changes (skips already configured ones)**
- Saves all diffs and summaries to timestamped files

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
- **List interfaces that already have port-profiles applied (will be skipped) in condensed ranges**
- **List interfaces that need configuration in condensed ranges**
- Show which interfaces would be configured
- Display the exact commands that would be executed
- Skip actual configuration changes
- Skip MAC table captures to save time
- **Not make any changes to the devices**
