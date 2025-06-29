package org.example.util;

import com.backblaze.erasure.ReedSolomon;
import org.example.model.Proposal;
import org.example.respository.ProposalRepository;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.annotation.Configuration;

import java.io.ByteArrayOutputStream;
import java.util.*;

@Configuration
public class OciorMvbahEngine {


    @Autowired
    private ProposalRepository proposalRepository;
    int id;
    int messageLength;

    static final int N = 5, T = 1;

    Set<String> allCommitments = new HashSet<>();

    Map<String, Map<Integer, byte[]>> confirmed = new HashMap<>();


    byte[] DRh(String C) throws Exception {
        Map<Integer, byte[]> shares = confirmed.get(C);
        if (shares == null || shares.size() < T + 1) return null;
        List<byte[]> subset = new ArrayList<>(shares.values());
        return decodeMessageWithReedSolomon(subset);

    }

    boolean predicate(byte[] msg) {
        return msg != null && msg.length > 0;
    }


    int ABBBA(Proposal proposal) {
        return Integer.parseInt(proposal.getValue());
    }

    int ABBA(int a) {
        return a; // simulate confirmation
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
    byte[][] encodeMessageWithReedSolomon(byte[] data) {
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
        // 1) load proposals into confirmed commitments like Node.receiveShare() does
        for (Proposal p : proposals) {
            byte[] value = p.getValue().getBytes();
            byte[][] shares = encodeMessageWithReedSolomon(value);
            String C = p.getCommitment();
            // simulate collecting t+1 shares (in real life, you'd get shares from network)
            Map<Integer, byte[]> dummyShares = new HashMap<>();
            for (int i = 0; i < T + 1; i++) {
                dummyShares.put(i, shares[i]);
            }
            confirmed.put(C, dummyShares);
            allCommitments.add(C);
        }

        // 2) run the same election → ABBBA → ABBA → DRh → Predicate logic
        Map<String, Integer> valueCounts = new HashMap<>();

        for (String C : allCommitments) {
            List<Proposal> proposalList = proposalRepository.findByCommitment(C);
            for (Proposal proposal : proposalList) {
                int a = ABBBA(proposal);
                int b = ABBA(a);
                if (b == 1 || b == 0) {
                    messageLength = proposalList.get(0).getValue().length();
                    byte[] val = DRh(C);
                    if (predicate(val)) {
                        String sVal = new String(val);
                        valueCounts.put(sVal, valueCounts.getOrDefault(sVal, 0) + 1);
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
        }

        return "⊥"; // No majority found
    }

}
