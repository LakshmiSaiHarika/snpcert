# Enabling AMD Security Features in AMD EPYC Processors

## SEV Introduction
When a virtual machine (VM) starts, data is loaded into system memory (RAM). The data can be vulnerable to software or hardware probing by attackers on the host system- especially in shared environments like cloud platforms, where multiple tenants share the same physical resources. To mitigate this risk, users must ensure that the data in-use is protected from both attackers and malicious hypervisors. Doing so reduces the level of trust that virtual machines need to place in the hypervisor and the host system's administrators.

AMD EPYC processors introduce confidential computing technologies that provide memory encryption for virtualized environments, protecting data not only from physical attacks but also from other virtual machines and even the hypervisor itself.

There are currently 3 different generations of SEV, each building on the previous generation and introducing new security capabilities and features:

**Legacy SEV (Secure Encrypted Virtualization)**  is the first generation of the security features. It protects KVM virtual machines (VMs) by transparently encrypting the memory of the VM using a unique key.

**ES (Encrypted State)** is the second generation of SEV. It adds CPU register encryption when a VM stops running, preventing the information leak from the CPU registers to components like the hypervisor.

**SNP (Secure Nested Paging)**  is the third generation of SEV. It adds strong memory integrity protection on top of SEV and ES to aid in preventing malicious hypervisor-based attacks(data replay, memory mapping and more) to create an isolated execution environment. SNP also introduces several additional optional security enhancements designed to support additional VM use models, offer stronger protection around interrupt behavior, and offer increased protection against recently disclosed side channel attacks. It also introduces a new attestation model that allows run-time attestation in SNP protected VMs.

## Configuring SNP
Users can utilize the following guides to set-up SNP in their system.

### 1. Host Configuration for the Host Users
Enable SNP in your host in order to launch SNP protected VMs.

#### SNP host requirements:

- AMD EPYC Processor: 7003 or newer.

- kernel version: 6.11 or newer.

#### Enable AMD's security feature(SEV) in the host BIOS
To enable SNP in BIOS you need to enable the following settings:

```
CBS -> CPU Common ->
            SEV-ES ASID Space Limit -> 100
            SNP Memory Coverage -> Enabled
            SMEE -> Enabled
    -> NBIO Common ->
            SEV-SNP -> Enabled
```
For a more in depth enablement guide, please take a look at the "Using SEV with AMD EPYC Processors" guide in our additional resources.

NOTE: The SNP options might differ depending on the server manufacturer and BIOS version. Please refer to your respective server manual to enable SNP options in the BIOS settings.

#### Verify SNP enablement

To verify the complete enablement of AMD’s security features (SEV, ES, and SNP) within their Linux host, users may utilize the [Virtee snphost](https://github.com/virtee/snphost) tool to assess SNP support and enablement on the system:
To use this tool:
1. Download the latest snphost release from [snphost GH Releases](https://github.com/virtee/snphost/releases) page.
2. Execute the command `snphost ok` to confirm the presence and status of the supported security features.

### 2. Guest Launch and enablement for the Guest Users

An SNP enabled guest can be launched after the host has properly set-up and enabled SNP.
The following are **guest** requirements to launch an SNP enabled VM:
   - Guest kernel version: 5.19+
   - QEMU version: 9.2+
   - OVMF version: 2024.11+

#### Guest Launch

Guest users may initiate SEV-SNP-enabled virtual machine boots using the QEMU hypervisor by utilizing the mainline release of one of the certified images in this repository. Please reference the table of certified images here: **[Certification Matrix](https://github.com/AMDEPYC/sev-certify#certification-matrix)**

To boot one of the mainline qcow2 images from one of the certified OS, the user can use a command similar to the following:
```sh
$ qemu-system-x86_64 \
    -enable-kvm \
    -machine q35 \
    -cpu EPYC-v4 \
    -machine memory-encryption=sev0 \
    -monitor none \
    -display none \
    -object memory-backend-memfd,id=ram1,size=<guest-ram-size> \
    -machine memory-backend=ram1 \
    -object sev-snp-guest,id=sev0,cbitpos=51,reduced-phys-bits=1,kernel-hashes=on \
    -bios <amdsev-ovmf-path> \
    -hda <path-to-guest-image>
```

**QEMU Command Line Options:**

| Option | Value | Description |
|--------|-------|-------------|
| `-enable-kvm` | - | Enables KVM full virtualization support, required for SEV functionality. |
| `-machine` | `q35` | Specifies the Q35 machine type, which provides PCIe support and is required for SEV. |
| `-cpu` | `EPYC-v4` | Sets the CPU model to AMD EPYC with SEV-SNP capabilities. |
| `-machine memory-encryption` | `sev0` | Links the VM to the SEV object (defined below) for memory encryption. |
| `-monitor` | `none` | Disables the QEMU monitor interface. |
| `-display` | `none` | Disables graphical display output. |
| `-object memory-backend-memfd` | `id=ram1,size=<guest-ram-size>` | Creates a memory backend using memfd, required for SEV encrypted memory. Minimum size: 2 GB (2048 MB). |
| `-machine memory-backend` | `ram1` | Associates the memory backend with the VM. |
| `-object sev-snp-guest` | `id=sev0,cbitpos=51,...` | Creates the SEV-SNP guest object with encryption settings. See detailed parameters below. |
| `-bios` | `<amdsev-ovmf-path>` | Path to the AMDSEV UEFI firmware: `/usr/share/ovmf/OVMF.amdsev.fd` or `/usr/share/edk2/ovmf/OVMF.amdsev.fd` depending on your distribution. |
| `-hda` | `<path-to-guest-image>` | Path to your guest disk image file (qcow2 format). |

#### Understanding the `-object sev-snp-guest` Command

The `-object sev-snp-guest` command in QEMU creates and configures an SEV-SNP guest object that enables the full suite of third-generation AMD memory encryption and integrity protection features. This object is essential for launching VMs with SNP protection.

**Key parameters for `sev-snp-guest`:**

| Parameter | Default | Description |
|-----------|---------|-------------|
| `id` | (required) | Unique identifier for the SEV object (e.g., `sev0`). This ID is referenced by `-machine memory-encryption=<id>`. |
| `cbitpos` | `51` | The C-bit (encryption bit) position in the guest physical address. The C-bit indicates whether a memory page is encrypted. Value `51` is correct for all current AMD EPYC processors (7003/7004/9004 series). |
| `reduced-phys-bits` | `1` | The number of physical address bits lost due to encryption. Value `1` means the guest loses 1 bit of physical address space (halving the maximum addressable memory), which is required for the encryption metadata. |
| `kernel-hashes` | `off` | When `off` (default), the kernel, initrd, and command line are not measured. When `on`, their hashes are included in the attestation measurement, enabling measured direct boot for enhanced security verification. |
| `policy` | `0x30000` | A 64-bit guest policy controlling VM behavior. The default `0x30000` sets bits 16-17, which require SMT (Simultaneous Multi-Threading) to be disabled on the host for security. See policy bit definitions below. |
| `guest-visible-workarounds` | `0` (none) | 16-byte hex string for guest-visible workarounds. Default of `0` means no workarounds are applied. Used to communicate known hardware errata mitigations to the guest. |
| `id-block` | (none) | Path to a file containing the 96-byte ID block for the guest. When not specified, no ID block is used, allowing any guest owner. When provided, enables guest owner identification and verification. |
| `id-auth` | (none) | Path to a file containing the ID authentication information. Required when `id-block` is specified. Contains cryptographic signatures to verify the ID block authenticity. |
| `host-data` | `0` (empty) | 32-byte hex string of host-provided data included in the attestation report. Default empty means no host data is included. Can be used to bind the VM to specific host configurations or pass context to verifiers. |

**Policy bit definitions (for `policy` parameter):**

The 64-bit policy value controls security requirements for the SNP guest :

| Bit(s) | Name | Meaning when set |
|--------|------|------------------|
| 0:7  | `ABI_MINOR` | The minimum ABI minor version required for this guest to run. |
| 15:8 | `ABI_MAJOR` | The minimum ABI major version required for this guest to run.|
| 16 | `SMT` | 0: SMT is disallowed.  |
|   |   | 1: SMT is allowed. |
| 17 | `–` | Reserved. Must be one. |
| 18 | `MIGRATE_MA` | 0: Association with a migration agent is disallowed. |
|   |   | 1: Association with a migration agent is allowed. |
| 19 | `DEBUG` | 0: Debugging is disallowed. |
|   |   | 1: Debugging is allowed. |
| 20 | `SINGLE_SOCKET` | 0: Guest can be activated on multiple sockets. |
|    |   | 1: Guest can be activated only on one socket. |
| 21 | `CXL_ALLOW` | 0: CXL cannot be populated with devices or memory. |
|  |   | 1: CXL can be populated with devices or memory.  |
| 22 | `MEM_AES_256_XTS ` | 0: Allow either AES 128 XEX or AES 256 XTS for memory encryption. |
|   |   | 1: Require AES 256 XTS for memory encryption. |
| 23 | `RAPL_DIS` | 0: Allow Running Average Power Limit (RAPL). |
|    |   | 1: RAPL must be disabled. |
| 24 | `CIPHERTEXT_HIDING_DRAM` | 0: Ciphertext hiding for the DRAM may be enabled or disabled. |
|   |   | 1: Ciphertext hiding for the DRAM must be enabled.  |   |
| 25 | `PAGE_SWAP_DISABLE` | Guest policy to disable Guest access to SNP_PAGE_MOVE,SNP_SWAP_OUT and SNP_SWAP_IN commands. |
|    |    |  If this policy option is selected to disable these Page Move commands, then these commands will return POLICY_FAILURE.  |
|  |   | 0: Do not disable Guest support for the commands  |
|  |   | 1: Disable Guest support for the commands.  |
| 26:63 | `–` | Reserved. MBZ.  |

The default policy `0x30000` (bits 16-17 set) requires ABI major version 3 and does **not** set the SMT bit, meaning SMT must be disabled on the host for security.

#### Difference Between `sev-snp-guest` and Legacy `sev-guest`

QEMU provides different object types for different generations of AMD SEV technology:

| Feature | `sev-guest` (Legacy SEV/SEV-ES) | `sev-snp-guest` (SEV-SNP) |
|---------|--------------------------------|---------------------------|
| **Memory Encryption** | ✓ AES-128 encryption | ✓ AES-128 encryption |
| **Register State Encryption** | ✓ (SEV-ES only) | ✓ Included |
| **Memory Integrity Protection** | ✗ Not available | ✓ Full integrity via RMP |
| **Attestation Model** | Launch-time only | Runtime attestation supported |
| **Hypervisor Attack Protection** | Limited | Strong (replay, remapping attacks) |
| **Required Firmware** | `OVMF.fd` | `OVMF.amdsev.fd` |
| **Minimum Processor** | EPYC 7001 (Naples) | EPYC 7003 (Milan) |

**Legacy `sev-guest` example (for SEV or SEV-ES):**
```sh
-object sev-guest,id=sev0,cbitpos=51,reduced-phys-bits=1,policy=0x1
```

**SNP `sev-snp-guest` example (recommended for new deployments):**
```sh
-object sev-snp-guest,id=sev0,cbitpos=51,reduced-phys-bits=1,kernel-hashes=on
```

**When to use each:**
- Use `sev-snp-guest` for new deployments requiring the highest level of protection, including memory integrity and runtime attestation. This is the recommended option for all AMD EPYC 7003 (Milan) and newer processors.
- Use `sev-guest` only when supporting legacy systems with EPYC 7001/7002 processors that do not have SNP capability, or when specific compatibility requirements mandate SEV or SEV-ES without SNP.

Guest users can refer to SEV [QEMU documentation](https://www.qemu.org/docs/master/system/i386/amd-memory-encryption.html) for the additional SEV guest capabilities.

### 3. SEV Certificates for the Verifiers
Verifiers seek to perform AMD' SEV validation checks to confirm the presence and functionality of AMD’s Secure Encrypted Virtualization features. These verifiers may include operating system vendors, hardware manufacturers, or OEMs evaluating support within their platforms, firmware, or pre-release operating systems.

A comprehensive list of operating systems that support AMD SEV features is available in the [Certification Matrix](https://github.com/AMDEPYC/sev-certify#certification-matrix). Additionally, verifiers may review detailed host and guest SEV status reports within the GitHub Issues section of the sev-certify repository, which are automatically generated by the [dispatch](https://github.com/AMDEPYC/dispatch.git) tool.

Verifiers may generate a new SEV certificate to evaluate the status of AMD SEV features on their specific hardware, firmware, or pre-release operating system by following the guidelines highlighted in [how-to-generate-certs documentation](../docs/how-to-generate-certs.md)

## Additional Resources

[AMD Secure Encrypted Virtualization Developer Central](https://www.amd.com/en/developer/sev.html)

[Using SEV with AMD EPYC Processors](https://www.amd.com/content/dam/amd/en/documents/epyc-technical-docs/tuning-guides/58207-using-sev-with-amd-epyc-processors.pdf)

[AMD Secure Encrypted Virtualization feature in QEMU](https://www.qemu.org/docs/master/system/i386/amd-memory-encryption.html)
