package org.example.controller;

import com.backblaze.erasure.ReedSolomon;
import org.example.model.Proposal;
import org.example.service.OciorMvbaRound;
import org.example.service.ProposalService;
import org.example.util.AcidhProtocol;
import org.example.util.MerkleTree;
import org.example.util.MvbaRoundWithElection;
import org.example.util.OciorMvbahAlgorithm8;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

@RestController
@RequestMapping("/api/proposals")
public class ProposalController {

    @Autowired
    private ProposalService service;

    @PostMapping
    public ResponseEntity<Proposal> create(@RequestBody Proposal proposal) throws Exception {
        return ResponseEntity.ok(service.createProposal(proposal));
    }

    @GetMapping
    public List<Proposal> all() throws Exception {

        List<Proposal> proposals= service.getAll();
        OciorMvbaRound ociorMvbaRound=new OciorMvbaRound();
        ociorMvbaRound.runMvbaRound(proposals);
        return proposals;
    }

    @GetMapping("/election")
    public String fetchAll() throws Exception {
        return "fetch";
    }

    @PostMapping("/decide")
    public ResponseEntity<String> decide() throws Exception {
        return ResponseEntity.ok(service.runConsensus());
    }

}
