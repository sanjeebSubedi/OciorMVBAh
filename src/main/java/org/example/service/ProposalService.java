package org.example.service;

import org.example.model.Proposal;
import org.example.respository.ProposalRepository;
import org.example.util.OciorMvbahEngine;
import org.example.util.ShareUtils;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.util.Arrays;
import java.util.List;

@Service
public class ProposalService {

    @Autowired
    private ProposalRepository repo;

    @Autowired
    private OciorMvbahEngine engine;

    public Proposal createProposal(Proposal p) throws Exception {
        p.setCommitment(ShareUtils.commit(p.getValue().getBytes()));
        return repo.save(p);
    }

    public List<Proposal> getAll() {
        return repo.findAll();
    }

    public String runConsensus() throws Exception {
        List<Proposal> proposals = repo.findAll();
        return engine.mvbahRound(proposals);
    }
}
