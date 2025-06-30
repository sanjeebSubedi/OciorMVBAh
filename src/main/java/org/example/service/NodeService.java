package org.example.service;

import org.example.model.Node;
import org.example.respository.NodeRepository;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

@Service
public class NodeService {

    @Autowired
    private NodeRepository nodeRepository;


    public Node createNode(Node node){
        return nodeRepository.save(node);
    }
}
