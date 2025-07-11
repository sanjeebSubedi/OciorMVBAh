# OciorMVBAh — Modular Python Implementation

This repository contains a **modular, production-ready implementation** of the **OciorMVBAh protocol**, a hash-based, error-free, asynchronous Multi-Valued Validated Byzantine Agreement (MVBA) protocol. The work is based on the research paper:

> **"OciorMVBA: Near-Optimal Error-Free Asynchronous MVBA"**  
> _Jinyuan Chen, arXiv:2501.00214_

---

## 🎓 Course Information

- **Course**: CSC 557-384 — Blockchain and Distributed Consensus
- **Program**: M.S. in Computer Science, Louisiana Tech University
- **Quarter**: Summer 2025
- **Instructor**: Dr. Jinyuan Chen

---

## 🏗️ Architecture Highlights

This implementation features a **clean, modular architecture** that separates concerns:

- **🔬 Framework-Agnostic Protocol Logic**: Pure state machines testable in isolation
- **🔌 Pluggable Components**: Easy to extend with new protocols
- **🧪 Comprehensive Testing**: Unit, integration, and system-level testing
- **📡 Robust Networking**: ZeroMQ-based P2P communication
- **🎯 Type Safety**: Strong typing throughout the codebase

### Key Features

- ✅ **Complete MVBA Implementation**: All phases (ACIDh, Election, ABBBA/ABBA, DRh)
- ✅ **Modular Design**: Separate protocol logic from networking and orchestration
- ✅ **Reed-Solomon Erasure Coding**: Efficient data encoding/decoding
- ✅ **Merkle Tree Commitments**: Cryptographic vector commitments
- ✅ **Multi-Round Support**: Handles multiple election rounds correctly
- ✅ **Byzantine Fault Tolerance**: Supports up to `t < n/3` Byzantine nodes

---

## 📁 Project Structure

```
ocior_mvbah/
├── protocol/                    # 🔬 Protocol implementations
│   ├── common/                  # Shared protocol utilities
│   │   ├── message.py          # Canonical message definitions
│   │   └── __init__.py
│   └── acid/                    # ACIDh protocol module
│       ├── state.py            # Pure ACIDh state machine
│       ├── handler.py          # Integration wrapper
│       └── __init__.py
├── crypto/                      # 🔐 Cryptographic utilities
│   ├── __init__.py             # Public API exports
│   ├── erasure_coding.py       # Reed-Solomon implementation
│   ├── vector_commitment.py    # Merkle tree commitments
│   └── README.md               # Crypto module documentation
├── core/                        # 🔌 Core networking components
│   └── router.py               # Message routing system
├── network/                     # 📡 Network layer abstractions
│   └── zmq_manager.py          # ZeroMQ networking implementation
├── node.py                      # 🎯 Main node orchestration
├── server.py                    # 🚀 Bootstrap server
├── predicate.py                # ✅ Input validation
└── OciorMVBAh.md               # 📚 Algorithm documentation
```

---

## ⚙️ Getting Started

### Prerequisites

- **Python 3.10+**
- **UV package manager** (recommended) or pip

### Installation

1. **Clone the repository**:

   ```bash
   git clone <repository-url>
   cd ocior_mvbah
   ```

2. **Install dependencies**:

   ```bash
   # Using UV (recommended)
   uv sync

   # Or using pip
   pip install -r requirements.txt
   ```

### Running the Protocol

#### 1. Start the Bootstrap Server

In one terminal, start the bootstrap server:

```bash
uv run server.py
# or: python server.py
```

The server will wait for 4 nodes to connect and initialize the network.

#### 2. Start the Nodes

In separate terminals, start each node:

```bash
# Terminal 1
uv run node.py

# Terminal 2
uv run node.py

# Terminal 3
uv run node.py

# Terminal 4
uv run node.py
```

**Input Validation**: When prompted, enter valid inputs like:

- `input_from_0`, `input_from_1`, `input_from_2`, `input_from_3`

#### 3. Watch the Protocol Execute

You'll see the complete MVBA protocol execution:

```
🔄 ACIDh Phase: Nodes share, vote, lock, ready, finish
🎲 Election Phase: Common coin selects leader
🔄 ABBBA/ABBA Phase: Byzantine agreement on leader's proposal
🎯 DRh Phase: Data retrieval and final decision
🎉 MVBA Decision: One node's input is selected
```

---

## 🧪 Testing & Validation

### Test Scenario 1: Normal Operation

**Setup**: All 4 nodes running normally

**Steps**:

1. Start bootstrap server
2. Start all 4 nodes in separate terminals
3. Enter valid inputs when prompted

**Expected Result**: Single-round consensus with successful MVBA decision

### Test Scenario 2: Single Node Failure

**Setup**: Simulate one node crash (within Byzantine threshold t=1)

**Steps**:

1. Start bootstrap server
2. Start all 4 nodes
3. **During protocol execution**, terminate one node (Ctrl+C)
4. Observe remaining 3 nodes continue

**Expected Result**: Protocol continues and reaches consensus with 3 nodes

### Test Scenario 3: Double Node Failure

**Setup**: Simulate two node crashes (exceeds Byzantine threshold)

**Steps**:

1. Start bootstrap server
2. Start all 4 nodes
3. **During protocol execution**, terminate two nodes (Ctrl+C)
4. Observe remaining 2 nodes behavior

**Expected Result**: **Protocol gets stuck** - this is correct behavior since 2 < 2t+1 = 3

⚠️ **Important**: This is the expected safety behavior when Byzantine assumptions are violated!

### Test Scenario 4: Leader Message Drop (Multi-Round)

**Setup**: Force leader failure to test round progression

**Steps**:

1. In `node.py`, set `TEST_FORCE_EXTRA_ROUND = True`
2. Start bootstrap server and 4 nodes
3. Observe protocol behavior

**Expected Result**: First round fails, protocol advances to round 2 with new leader and succeeds

### Test Scenario 5: Input Validation

**Setup**: Test predicate function enforcement

**Steps**:

1. Start normal 4-node setup
2. When prompted for input, try invalid values:
   - `"hello"` (rejected)
   - `"input_from_5"` (rejected - invalid node ID)
   - `"data_from_1"` (rejected - wrong prefix)
3. Then enter valid input: `"input_from_0"`

**Expected Result**: Invalid inputs rejected, valid inputs accepted

---

## 📊 Protocol Parameters

- **Default Configuration**: `n = 4, t = 1` (tolerates 1 Byzantine node)
- **Minimum Requirement**: `n ≥ 3t + 1`
- **Supported Configurations**: Any `n ≥ 4` with appropriate `t`

### Byzantine Fault Tolerance

The protocol guarantees:

- ✅ **Safety**: All honest nodes decide the same value
- ✅ **Liveness**: Decision reached despite up to `t` Byzantine nodes
- ✅ **Validity**: Decided value comes from an honest node's input

---

## 📚 Documentation

- **[OciorMVBAh.md](OciorMVBAh.md)**: Complete algorithm documentation with implementation notes
- **Code Comments**: Detailed inline documentation throughout
- **Type Hints**: Full type annotation for better development experience

---

## 📄 License

This project is developed for educational purposes as part of CSC 557-384 coursework.

## 📖 Reference

Chen, Jinyuan. "OciorMVBA: Near-Optimal Error-Free Asynchronous MVBA." arXiv:2501.00214 (2025).

---
