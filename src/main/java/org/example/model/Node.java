package org.example.model;

import com.fasterxml.jackson.annotation.JsonIgnore;
import jakarta.persistence.*;
import lombok.Data;

import java.util.List;


@Entity
@Table(name = "node")
@Data
public class Node {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "name")
    private String name;


    @Column(name = "hash")
    private String hash;

    @Column(name = "verified")
    private boolean verified;

    @Column(name = "proposals")
    @OneToMany(mappedBy = "node")
    @JsonIgnore
    private List<Proposal> proposals;
}
