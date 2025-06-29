package org.example.respository;

import org.example.model.Proposal;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.Optional;

public interface ProposalRepository extends JpaRepository<Proposal, Long> {

    List<Proposal> findByCommitment(String commitment);

}
