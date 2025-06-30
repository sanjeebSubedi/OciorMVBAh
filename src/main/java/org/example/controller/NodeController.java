package org.example.controller;

import org.example.model.Node;
import org.example.service.NodeService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/nodes")
public class NodeController {


    @Autowired
    private NodeService nodeService;


    @PostMapping
    public ResponseEntity<Node> create(@RequestBody Node node){
        return ResponseEntity.ok(nodeService.createNode(node));
    }


}
