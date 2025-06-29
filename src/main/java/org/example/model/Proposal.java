package org.example.model;


import jakarta.persistence.*;
import lombok.Data;

@Entity
@Table(name = "proposal")
@Data
public class Proposal {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    private String proposer;
    private String value;
    private String commitment;

}
