package org.example.respository;

import org.example.model.Proposal;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.Optional;

@Repository
public interface ProposalRepository extends JpaRepository<Proposal, Long> {

    List<Proposal> findByCommitment(String commitment);

    List<Proposal> findByNodeId(Long nodeId);

}
