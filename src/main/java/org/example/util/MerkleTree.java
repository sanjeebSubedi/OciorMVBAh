package org.example.util;

import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

public class MerkleTree {

    private final List<byte[]> leaves;
    private final List<List<byte[]>> treeLevels; // each level: list of hashes from bottom (level 0=leaves) to root.

    public MerkleTree(List<byte[]> leaves) throws Exception {
        this.leaves = leaves;
        this.treeLevels = new ArrayList<>();
        buildTree();
    }

    private void buildTree() throws Exception {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        List<byte[]> currentLevel = new ArrayList<>();
        for (byte[] leaf : leaves) {
            currentLevel.add(digest.digest(leaf));
        }
        treeLevels.add(currentLevel); // level 0: hashed leaves

        while (currentLevel.size() > 1) {
            List<byte[]> nextLevel = new ArrayList<>();
            for (int i = 0; i < currentLevel.size(); i += 2) {
                byte[] left = currentLevel.get(i);
                byte[] right = (i + 1 < currentLevel.size()) ? currentLevel.get(i + 1) : left; // duplicate if odd
                nextLevel.add(digest.digest(concat(left, right)));
            }
            treeLevels.add(nextLevel);
            currentLevel = nextLevel;
        }
    }

    public byte[] buildRoot() {
        List<byte[]> top = treeLevels.get(treeLevels.size() - 1);
        return top.get(0);
    }

    public List<byte[]> getProof(int index) {
        List<byte[]> proof = new ArrayList<>();
        int idx = index;
        for (int level = 0; level < treeLevels.size() - 1; level++) {
            List<byte[]> currentLevel = treeLevels.get(level);
            int siblingIndex = idx ^ 1; // sibling is idx's neighbor: idx^1 flips last bit (0↔1)
            if (siblingIndex < currentLevel.size()) {
                proof.add(currentLevel.get(siblingIndex));
            } else {
                // if sibling doesn't exist (odd node without pair), duplicate current node as sibling
                proof.add(currentLevel.get(idx));
            }
            idx /= 2; // move up the tree
        }
        return proof;
    }

    private static byte[] concat(byte[] a, byte[] b) {
        byte[] result = new byte[a.length + b.length];
        System.arraycopy(a, 0, result, 0, a.length);
        System.arraycopy(b, 0, result, a.length, b.length);
        return result;
    }

    public static boolean verifyProof(int index, byte[] leaf, List<byte[]> proof, byte[] root) throws Exception {

        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        byte[] hash = digest.digest(leaf);
        int idx = index;
        for (byte[] sibling : proof) {
            hash = (idx % 2 == 0) ? digest.digest(concat(hash, sibling))
                    : digest.digest(concat(sibling, hash));
            idx /= 2;
        }
        return Arrays.equals(hash, root);
    }
}
