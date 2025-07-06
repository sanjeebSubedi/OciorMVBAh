package org.example.util;

import com.backblaze.erasure.ReedSolomon;
import org.example.model.Node;
import org.example.model.Proposal;
import org.example.respository.NodeRepository;
import org.example.respository.ProposalRepository;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.annotation.Configuration;

import java.io.ByteArrayOutputStream;
import java.util.*;

@Configuration
public class OciorMvbahEngine {


    @Autowired
    private ProposalRepository proposalRepository;
    int messageLength;

    static int N = 5;
    static final int T = 1;

    Set<String> allCommitments = new HashSet<>();

    Map<String, Map<Integer, byte[]>> confirmed = new HashMap<>();

    Map<Integer, byte[][]> S_shares = new HashMap<>();
    Map<Integer, Integer> L_lock = new HashMap<>();
    Map<Integer, Integer> R_ready = new HashMap<>();
    Map<Integer, Integer> F_finish = new HashMap<>();
    Map<Integer, String> H_hash = new HashMap<>();

    @Autowired
    private NodeRepository nodeRepository;

    byte[] DRh(String C) throws Exception {
        Map<Integer, byte[]> shares = confirmed.get(C);
        if (shares == null || shares.size() < T + 1) return null;
        List<byte[]> subset = new ArrayList<>(shares.values());
        return decodeMessageWithReedSolomon(subset);

    }

    boolean predicate(byte[] msg) {
        return msg != null && msg.length > 0;
    }


    int ABBBA(Proposal proposal, List<Proposal> proposals) {
        int quorum = (2 * T) + 1;
        long zeros, ones;
        zeros=proposals.stream().filter(proposal1 -> proposal1.getValue().equalsIgnoreCase("0")).count();
        ones=proposals.stream().filter(proposal1 -> proposal1.getValue().equalsIgnoreCase("1")).count();
        if (proposal.getValue().equalsIgnoreCase("0") && zeros >= quorum) {
            return 0;
        }
       else if (proposal.getValue().equalsIgnoreCase("1") && ones >= quorum) {
            return 1;
        }
       return -1;
    }

    int ABBA(int a) {
        if (a == 0 || a == 1) {
            return a; // quorum reached → keep decided value
        } else {
            // Fallback: use common coin when undecided
            int coin = new java.util.Random().nextBoolean() ? 1 : 0;
            System.out.printf("⚖️ ABBA: no quorum → using common coin: %d%n", coin);
            return coin;
        }
    }


    byte[] decodeMessageWithReedSolomon(List<byte[]> subset) throws Exception {
        int shardSize = subset.get(0).length;
        byte[][] shards = new byte[N][shardSize];
        boolean[] shardPresent = new boolean[N];

        // Place received shares into the first requiredShares slots
        for (int i = 0; i < subset.size(); i++) {
            shards[i] = subset.get(i);
            shardPresent[i] = true;
        }

        ReedSolomon reedSolomon = ReedSolomon.create(T, N - T);

        // Attempt recovery
        reedSolomon.decodeMissing(shards, shardPresent, 0, shardSize);

        // Combine recovered data shards back into message
        ByteArrayOutputStream result = new ByteArrayOutputStream();
        for (int i = 0; i < OciorMvbahEngine.T; i++) result.write(shards[i]);
        return result.toByteArray();
    }
   static byte[][] encodeMessageWithReedSolomon(byte[] data) {
        int shardSize = (data.length + T - 1) / T; // minimum to cover data

        // Total data + parity shards
        int parityShards = OciorMvbahEngine.N - T;

        // Initialize shards (zero-padded)
        byte[][] shards = new byte[OciorMvbahEngine.N][shardSize];
        for (int i = 0; i < T; i++) {
            int copyLen = Math.min(shardSize, data.length - i * shardSize);
            System.arraycopy(data, i * shardSize, shards[i], 0, Math.max(copyLen, 0));
        }

        // Create Reed-Solomon encoder
        ReedSolomon reedSolomon = ReedSolomon.create(T, parityShards);
        reedSolomon.encodeParity(shards, 0, shardSize);  // ✅ the only correct method to generate parity

        return shards;
    }

    public String mvbahRound(List<Proposal> proposals) throws Exception {
        List<Node> nodes=nodeRepository.findAll();
        if(!nodes.isEmpty()){
            N=nodes.size();
        }
        // 1) load proposals into confirmed commitments like Node.receiveShare() does
        for (Proposal p : proposals) {
            initNodeStates(N);
            byte[] value = p.getValue().getBytes();
            byte[][] shares = encodeMessageWithReedSolomon(value);
            Integer nodeId = p.getNode().getId().intValue();

            String C = p.getCommitment();
            // simulate collecting t+1 shares (in real life, you'd get shares from network)
            Map<Integer, byte[]> dummyShares = new HashMap<>();
            for (int i = 0; i < T + 1; i++) {
                dummyShares.put(i, shares[i]);
                S_shares.get(nodeId)[i] = shares[i];
            }
            confirmed.put(C, dummyShares);
            allCommitments.add(C);
            H_hash.put(nodeId, p.getCommitment());
        }

        // 2) run the same election → ABBBA → ABBA → DRh → Predicate logic
        Map<String, Integer> valueCounts = new HashMap<>();

        for (String C : allCommitments) {
            List<Proposal> proposalList = proposalRepository.findByCommitment(C);
            for (Proposal proposal : proposalList) {
                int a = ABBBA(proposal, proposalList);
                int b = ABBA(a);
                if (b == 1 || b == 0) {
                    messageLength = proposalList.get(0).getValue().length();
                        byte[] val = DRh(C);
                        if (predicate(val)) {
                            String sVal = new String(val);
                            valueCounts.put(sVal, valueCounts.getOrDefault(sVal, 0) + 1);
                            R_ready.put(proposal.getNode().getId().intValue(), 1);
                            L_lock.put(proposal.getNode().getId().intValue(), 1);
                    }
                }
            }
        }

        // Determine majority value
        int majorityThreshold = (proposals.size() / 2) + 1;
        for (Map.Entry<String, Integer> entry : valueCounts.entrySet()) {
            if (entry.getValue() >= majorityThreshold) {
                return entry.getKey();
            }
//            for (int nodeId = 1; true; nodeId++) {
//                if (R_ready.getOrDefault(nodeId, 0) == 1) {
//                    F_finish.put(nodeId, 1); // ✅ node has finished
//                }
//            }
        }
        return "⊥";
    }


    void initNodeStates(int n) {
        for (int j = 1; j <= n; j++) {
            S_shares.put(j, new byte[][]{null, null, null});
            L_lock.put(j, 0);
            R_ready.put(j, 0);
            F_finish.put(j, 0);
            H_hash.put(j, null);
        }
    }

}
