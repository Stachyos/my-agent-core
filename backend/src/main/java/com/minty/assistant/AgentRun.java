package com.minty.assistant;

import jakarta.persistence.*;
import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "agent_runs", indexes = @Index(name = "run_session_idx", columnList = "sessionId,createdAt"))
public class AgentRun {
    @Id public String id;
    @Column(nullable = false) public String sessionId;
    @Column(nullable = false, length = 16) public String status;
    @Column(columnDefinition = "text") public String error;
    public long inputTokens;
    public long outputTokens;
    @Column(nullable = false) public Instant createdAt;
    public Instant finishedAt;
    protected AgentRun() {}
    AgentRun(String sessionId) {
        this.id = UUID.randomUUID().toString(); this.sessionId = sessionId;
        this.status = "running"; this.createdAt = Instant.now();
    }
}
