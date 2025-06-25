# OciorMVBAh Algorithm Implementation

This document contains the formatted algorithms for the Multi-Value Validated Byzantine Agreement (MVBAH) protocol as described in the research paper.

## Table of Contents

- [Algorithm 8: OciorMVBAh Protocol](#algorithm-8-ociormvbah-protocol)
- [Algorithm 9: ACIDh Subprotocol](#algorithm-9-acidh-subprotocol)
- [Algorithm 10: DRh Subprotocol](#algorithm-10-drh-subprotocol)

---

## Algorithm 8: OciorMVBAh Protocol

**Protocol:** OciorMVBAh protocol with identifier `ID`, for `n ≥ 3t + 1`  
**Code shown for:** Process `Pi`

### Implementation Notes

> **Merkle Tree Implementation:** Merkle tree is implemented here for vector commitment based on hashing
>
> **Key Functions:**
>
> - `VcCom()` outputs a commitment (i.e., Merkle root) with O(κ) bits
> - `VcOpen()` returns a proof that the targeted value is the committed element of the vector
> - `VcVerify(j, C, y, ω)` returns true only if ω is a valid proof that C is the commitment of a vector whose jth element is y

### Algorithm Steps

```pseudocode
1: upon receiving MVBA input message wi and Predicate(wi) = true do:
2:     [Llock, Rready, Ffinish, Sshares] ← ACIDh[ID](wi)  // a protocol for n parallel ACID instances
3:     for r ∈ [1 : n] do
4:         l ← Election[(ID, r)]  // an election protocol
5:         a ← ABBBA[(ID, l)](Rready[l], Ffinish[l])  // asynchronous biased binary Byzantine agreement (ABBBA)
6:         b ← ABBA[(ID, l)](a)  // an asynchronous binary Byzantine agreement (ABBA)
7:         if b = 1 then
8:             ŵl ← DRh[(ID, l)](Llock[l], Sshares[l])  // Data Retrieval (DR)
9:             if Predicate(ŵl) = true then
10:                output ŵl and terminate
```

---

## Algorithm 9: ACIDh Subprotocol

**Protocol:** ACIDh subprotocol with identifier `ID`, based on hashing  
**Code shown for:** Process `Pi`

### Implementation Notes

> **ACID Protocol Overview:**
>
> - `ACID[ID]` is a protocol for n parallel ACID instances: `ACID[(ID, 1)], ACID[(ID, 2)], ..., ACID[(ID, n)]`
> - `ACID[(ID, j)]` is an ACID instance for delivering the message proposed from Node j
> - Once Node j completes `ACID[(ID, j)]`, there exists a retrieval scheme to correctly retrieve the message
> - When an honest node returns and stops this protocol, then at least `n − t` ACID instances have been completed
> - `ECEnc()` and `ECDec()` are encoding function and decoding function of `(n, k)` erasure code

### Algorithm Steps

#### Initialization

```pseudocode
1: // initialization:
2: Llock ← {}; Rready ← {}; Ffinish ← {}; Sshares ← {}; Hhash ← {}
3: for j ∈ [1 : n] do
4:     Sshares[j] ← (⊥, ⊥, ⊥); Llock[j] ← 0; Rready[j] ← 0; Ffinish[j] ← 0
```

#### ACID-Share Phase

```pseudocode
5: upon receiving input message wi do:
6:     [y1, y2, ..., yn] ← ECEnc(n, t + 1, wi)
7:     C ← VcCom([y1, y2, ..., yn])
8:     for j ∈ [1 : n] do
9:         ωj ← VcOpen(C, yj, j)
10:        send ("SHARE", ID, C, yj, ωj) to Pj
```

#### ACID-Vote Phase

```pseudocode
11: upon receiving ("SHARE", ID, C, y, ω) from Pj for the first time do:
12:     if VcVerify(i, C, y, ω) = true then
13:         Sshares[j] ← (C, y, ω); Hhash[C] ← j
14:         send ("VOTE", ID, C) to all nodes
```

#### ACID-Lock Phase

```pseudocode
15: upon receiving n − t ("VOTE", ID, C) messages from distinct nodes, for the same C do:
16:     wait until C ∈ Hhash
17:     j⋆ ← Hhash[C]; Llock[j⋆] ← 1
18:     send ("LOCK", ID, C) to all nodes
```

#### ACID-Ready Phase

```pseudocode
19: upon receiving n − t ("LOCK", ID, C) messages from distinct nodes, for the same C do:
20:     wait until C ∈ Hhash
21:     j⋆ ← Hhash[C]; Rready[j⋆] ← 1
22:     send ("READY", ID, C) to all nodes
```

#### ACID-Finish Phase

```pseudocode
23: upon receiving n − t ("READY", ID, C) messages from distinct nodes, for the same C do:
24:     wait until C ∈ Hhash
25:     j⋆ ← Hhash[C]; Ffinish[j⋆] ← 1
26:     send ("FINISH", ID) to Pj⋆
```

#### Election Phase

```pseudocode
27: upon receiving n − t ("FINISH", ID) messages from distinct nodes do:
28:     send ("ELECTION", ID) to all nodes  // ACID[(ID, i)] is complete at this point

29: upon receiving n − t ("ELECTION", ID) messages from distinct nodes and ("CONFIRM", ID) not yet sent do:
30:     send ("CONFIRM", ID) to all nodes

31: upon receiving t + 1 ("CONFIRM", ID) messages from distinct nodes and ("CONFIRM", ID) not yet sent do:
32:     send ("CONFIRM", ID) to all nodes
```

#### Termination

```pseudocode
33: upon receiving 2t + 1 ("CONFIRM", ID) messages from distinct nodes do:
34:     if ("CONFIRM", ID) not yet sent then
35:         send ("CONFIRM", ID) to all nodes
36:     return [Llock, Rready, Ffinish, Sshares]
```

---

## Algorithm 10: DRh Subprotocol

**Protocol:** DRh subprotocol with identifier `id = (ID, l)`, based on hashing  
**Code shown for:** Process `Pi`

### Algorithm Steps

#### Initialization

```pseudocode
1: Initially set YSymbols[l] ← {}
```

#### Main Protocol Logic

```pseudocode
2: upon receiving input (lock_indicator, share) do:
3:     if (lock_indicator = 1) ∧ (share ≠ (⊥, ⊥, ⊥)) then
4:         (C⋆, y⋆, ω⋆) ← share
5:         send ("ECHOSHARE", ID, l, C⋆, y⋆, ω⋆) to all nodes
6:         wait for |YSymbols[l][C]| = t + 1 for some C
7:         ŵ ← ECDec(n, t + 1, YSymbols[l][C])
8:         if VcCom(ECEnc(n, t + 1, ŵ)) = C then
9:             return ŵ
10:        else
11:            return ⊥
```

#### Echo Share Handling

```pseudocode
12: upon receiving ("ECHOSHARE", ID, l, C, y, ω) from Pj for the first time, for some C, y, ω do:
13:     if VcVerify(j, C, y, ω) = true then
14:         if C ∉ YSymbols[l] then
15:             YSymbols[l][C] ← {j : y}
16:         else
17:             YSymbols[l][C] ← YSymbols[l][C] ∪ {j : y}
```

---

## Key Notation

- **n**: Total number of nodes
- **t**: Maximum number of Byzantine nodes (where `n ≥ 3t + 1`)
- **ID**: Protocol identifier
- **Pi**: Process/Node i
- **⊥**: Bottom/null value
- **ŵ**: Estimated/recovered message
- **C**: Commitment (Merkle root)
- **ω**: Proof/witness
- **j⋆**: Selected node index

## Message Types

- **SHARE**: Share erasure-coded data with vector commitment proof
- **VOTE**: Vote for a commitment
- **LOCK**: Lock a commitment
- **READY**: Signal readiness for a commitment
- **FINISH**: Finish processing for a commitment
- **ELECTION**: Vote in leader election
- **CONFIRM**: Confirm election result
- **ECHOSHARE**: Echo share in data retrieval phase
