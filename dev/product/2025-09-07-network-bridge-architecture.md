# Network Bridge Architecture Epic

**Date:** 2025-09-07

**Status:** In Progress

**Epic:** Standardize `eddings` networking on Linux bridges for consistent VM communication.


## Project Overview

Migrate `eddings` server networking from mixed macvtap/direct interface architecture
  to a unified Linux bridge-based approach.
This change eliminates host-VM communication barriers and establishes a consistent,
  scalable networking foundation for current and future virtualization needs.


## Goals & Requirements

### Primary Goals

1. **Enable full host-VM communication** by eliminating macvtap isolation barriers.
   - Critical for SSH and RDP access to VMs when connecting via `eddings` as VPN gateway.
   - Required for Ansible management of VMs from `eddings` host.

2. **Establish consistent networking architecture** across all `eddings` interfaces.
   - WAN interface already uses bridge (br0), extend pattern to LAN interface.
   - Simplify mental model and troubleshooting procedures.

3. **Improve operational management and troubleshooting**.
   - Standard bridge tools (`brctl`, `ip bridge`) for all interfaces.
   - Consistent configuration patterns across network interfaces.
   - Better visibility into traffic flows and connectivity issues.

4. **Future-proof for additional VMs and networking requirements**.
   - Support multiple VMs without communication restrictions.
   - Enable advanced networking features (VLANs, trunking) if needed.
   - Maintain flexibility for evolving homelab requirements.

### Key Requirements

- **Zero functional regression**: All existing services must continue operating.
- **Minimal performance impact**: No noticeable degradation in network performance.
- **Reliable migration path**: Safe deployment with rollback capability.
- **Consistent naming**: Use descriptive bridge names (`br-wan`, `br-lan`).


## Current State Analysis

### Existing Configuration

**WAN Interface (eno1):**

- Already bridged as `br0`.
- Hosts public IP addresses (`96.86.32.137`, `96.86.32.139`).
- Works well for public-facing services.

**LAN Interface (enp4s0):**

- Direct DHCP configuration (`10.0.0.2`).
- VMs use macvtap with `source_mode=bridge`.
- **Problem**: Macvtap blocks host-VM communication by design.

### Communication Barriers

The macvtap configuration creates an isolation layer that prevents:

- SSH access to krout VM (`10.0.0.5`) from `eddings` host.
- RDP connectivity for GUI development workflow.
- Ansible management of VMs from the host system.
- Direct troubleshooting and monitoring of VM services.

This becomes critical when accessing the homelab remotely via `eddings` as VPN gateway,
  where the communication path must traverse the `eddings` host.


## Desired Architecture

### Target Configuration

**WAN Bridge (`br-wan`):**

- Bridge the `eno1` interface (renamed from `br0`).
- Maintain public IP configuration.
- No functional changes to WAN connectivity.

**LAN Bridge (`br-lan`):**
- Bridge the `enp4s0` interface (new configuration).
- DHCP client on bridge interface.
- VMs attach directly to bridge (no macvtap).

### Benefits Over Current Approach

**Technical Benefits:**

- **Direct host-VM communication**: Eliminates macvtap isolation barrier.
- **Standard Linux networking**: Uses well-established kernel bridge implementation.
- **Simplified troubleshooting**: Consistent tools and procedures across interfaces.
- **Enhanced visibility**: Better network monitoring and debugging capabilities.

**Operational Benefits:**

- **Ansible compatibility**: Enables VM management from `eddings` host.
- **Remote access reliability**: SSH/RDP works consistently via VPN.
- **Scalability**: Supports additional VMs without architectural changes.
- **Maintenance efficiency**: Unified configuration and management approach


## Impact Assessment

### Performance Considerations

**Expected Impact:** Negligible performance overhead.

- Modern Linux bridges operate at near line-rate speeds.
- Typical overhead: <1ms latency, no throughput reduction.
- Home network usage patterns won't approach interface limits.
- VM workloads are development-focused, not performance-critical.

### Security Model

**Network Isolation:** Maintains appropriate security boundaries.

- VMs communicate at Layer 2 (equivalent to physical switch).
- No additional attack surface introduced by bridges.
- Standard enterprise virtualization security model.
- Broadcast domain limited to home network (minimal broadcast traffic).

### Reliability Assessment

**Stability:** Improved reliability over macvtap.

- Linux bridges: 20+ years of production use, battle-tested.
- Simpler networking path reduces potential failure points.
- Better integration with standard networking tools and monitoring.
- Consistent behavior patterns across both WAN and LAN interfaces.

## Success Criteria

### Technical Validation

- [ ] `eddings` host can SSH directly to krout VM (`10.0.0.5`).
- [ ] RDP connectivity to krout works via `eddings` VPN gateway.
- [ ] All existing services maintain full functionality.
- [ ] Network performance meets or exceeds current levels.
- [ ] Standard bridge tools provide visibility into VM traffic.

### Operational Validation

- [ ] Ansible can manage krout VM from `eddings` host.
- [ ] Remote access to krout works consistently when VPN'd through `eddings`.
- [ ] Network troubleshooting uses consistent tools and procedures.
- [ ] Configuration is maintainable and well-documented.

### Migration Validation

- [ ] Deployment completes without extended downtime.
- [ ] All public services remain accessible during and after migration.
- [ ] Rollback procedures tested and verified functional.
- [ ] No regressions in existing functionality or performance.


## Strategic Context

This architecture change directly supports the **GUI Development VM Epic**
  by removing the primary technical barrier to remote desktop access.
The decision to standardize on bridges creates a foundation
  for future homelab expansion while solving immediate operational challenges.

The migration represents a shift from a mixed networking approach to a consistent,
  enterprise-standard architecture that will serve as the
  foundation for future virtualization and networking requirements.

---

**Status**: ✅ Architecture Defined | 🔄 Implementation Planning