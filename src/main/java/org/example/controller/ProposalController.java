package org.example.controller;

import org.example.model.Proposal;
import org.example.service.ProposalService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

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
    public List<Proposal> all() {
        return service.getAll();
    }

    @PostMapping("/decide")
    public ResponseEntity<String> decide() throws Exception {
        return ResponseEntity.ok(service.runConsensus());
    }

}
