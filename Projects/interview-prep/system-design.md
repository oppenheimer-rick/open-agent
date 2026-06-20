# System Design Interview Prep — Shakib S.

## 1. Core Principles

### Scalability Fundamentals
- **Vertical scaling** (bigger machine) → limited by hardware
- **Horizontal scaling** (more machines) → requires stateless services, load balancing
- **Caching** at every layer: CDN → Edge → API Gateway → Service → DB
- **Asynchronous processing** for non-critical paths (emails, analytics, notifications)

### CAP Theorem
- **Consistency**: all nodes see same data
- **Availability**: every request gets a response
- **Partition Tolerance**: system handles network failures
- Reality: you can't have all three → choose CP or CA based on use case

---

## 2. API Design Patterns

### REST vs GraphQL vs gRPC

| Aspect | REST | GraphQL | gRPC |
|--------|------|---------|------|
| Protocol | HTTP | HTTP/WS | HTTP/2 |
| Payload | JSON | Query-based | Protobuf (binary) |
| Flexibility | Over-fetching | Precise queries | Streaming |
| Best for | CRUD APIs | Aggregations | Microservices |

### RESTful API Best Practices
```typescript
// Resource-oriented URLs
GET    /api/v1/users        // list
GET    /api/v1/users/:id    // detail
POST   /api/v1/users        // create
PUT    /api/v1/users/:id    // update
DELETE /api/v1/users/:id    // delete

// Pagination & filtering
GET /api/v1/messages?limit=20&offset=0&sort=-createdAt
```

### Interview Questions

1. **"How do you design an API for a chat application?"**
   - REST: `POST /messages`, `GET /channels/:id/messages`
   - Real-time: WebSocket or Server-Sent Events (SSE) for live updates
   - Rate limiting per user/channel
   - Message deduplication with idempotency keys

---

## 3. Database Design

### SQL vs NoSQL Decision Matrix

| Need | Choose |
|------|--------|
| Transactions, ACID | PostgreSQL |
| Schema flexibility | MongoDB |
| Time-series data | InfluxDB / TimescaleDB |
| Graph relationships | Neo4j |
| Key-value caching | Redis |
| Vector similarity (AI) | pgvector, Pinecone, Weaviate |

### Database Patterns

**N+1 Query Problem:**
```typescript
// Bad: N queries for N users
users.forEach(async user => {
    await db.message.findMany({ where: { userId: user.id } })
})

// Good: Batch load
const messages = await db.message.findMany({
    where: { userId: { in: userIds } },
    groupBy: 'userId'
})
```

**Database Indexing:**
- B-tree indexes: exact matches, range queries
- Hash indexes: equality only
- Partial indexes: `WHERE status = 'active'`
- Covering indexes: include frequently accessed columns

### Interview Questions

1. **"How do you handle database migrations in production?"**
   - Zero-downtime strategy: add column → backfill → switch app → drop old column
   - Use Prisma migration tool or Flyway/Liquibase
   - Test migrations on staging with production-scale data
   - Rollback plan: always have a revert script

2. **"When would you choose Redis over in-memory state?"**
   - Redis: distributed cache, pub/sub, rate limiting, session store
   - In-memory (Zustand): client-side state only, no persistence
   - Cache invalidation strategies: TTL, write-through, cache-aside

---

## 4. Distributed Systems Patterns

### Load Balancing Strategies
- **Round-robin**: simple, even distribution
- **Least connections**: route to least-loaded server
- **Consistent hashing**: minimize rebalancing on scale-up/down

### Caching Layers
```
Client → CDN → API Gateway (cache) → Service Cache (Redis) → Database
         ↑        ↑                      ↑
       Image    JSON response          Query results
```

### Circuit Breaker Pattern
```typescript
// Prevent cascading failures
const circuitBreaker = new CircuitBreaker({
    window: 5000,
    threshold: 3,  // open after 3 failures
    reset: 10000   // try again after 10s
})

try {
    await circuitBreaker.execute(() => api.call())
} catch {
    // Fallback response
}
```

### Interview Questions

1. **"Design a system to handle 1M concurrent WebSocket connections"**
   - Horizontal scaling with sticky sessions (or stateless auth)
   - Redis pub/sub for cross-server message broadcasting
   - Connection health checks + auto-reconnect
   - Message queue (Kafka/RabbitMQ) for fan-out to multiple servers

2. **"How do you handle distributed transactions?"**
   - Saga pattern: sequence of compensating transactions
   - Eventual consistency with outbox pattern
   - Idempotent operations with deduplication keys
   - Two-phase commit (rarely used in web scale)

---

## 5. RAG & AI System Architecture

### Your Domain — Key Patterns

**RAG Pipeline:**
```
Documents → Embedding Model → Vector DB → Query → Retrieval → LLM + Context → Response
```

**Component Decisions:**
- **Embedding**: OpenAI `text-embedding-3-small`, BGE, or sentence-transformers
- **Vector DB**: pgvector (Postgres), Pinecone, Weaviate, Qdrant
- **LLM**: GPT-4o, Claude 3.5 Sonnet, open-source alternatives
- **Chunking strategy**: semantic chunking vs fixed-size vs recursive

### Interview Questions

1. **"How do you evaluate RAG quality?"**
   - **Context precision**: % of retrieved chunks that are relevant
   - **Context recall**: % of all relevant documents that were retrieved
   - **Answer relevance**: does the final response actually answer the query?
   - Tools: RAGAS framework, LangSmith tracing

2. **"How do you handle hallucination in RAG?"**
   - Confidence scoring on retrieved context
   - "I don't know" fallback when no relevant context found
   - Citations: show source documents with the answer
   - Secondary LLM call to verify facts against retrieved context

---

## 6. Multi-Agent Systems

### Architecture Patterns

**Orchestrator Pattern:**
```
Coordinator Agent → Sub-agent A → Sub-agent B → Sub-agent C → Final Response
     (handles routing, planning, and aggregation)
```

**Router Pattern:**
```
Router Agent → { Agent A | Agent B | Agent C } (based on query classification)
```

**Peer-to-Peer Pattern:**
```
Agent A ↔ Agent B ↔ Agent C (shared knowledge base, no central coordinator)
```

### Interview Questions

1. **"How do you design an agent orchestration system?"**
   - Centralized orchestrator for complex multi-step tasks
   - Shared memory/context between agents
   - Fallback/retry mechanisms for each agent call
   - Cost monitoring + budget caps per agent type
   - Observability: trace each agent's input/output/conversation

2. **"How do you prevent infinite loops in agent systems?"**
   - Max iteration limits per task
   - Token budget enforcement
   - Step-by-step planning with explicit termination conditions
   - Observer pattern: external monitor can interrupt runaway agents

---

## 7. Security & Observability

### API Security Checklist
- [ ] Authentication (JWT, OAuth2)
- [ ] Rate limiting per user/IP
- [ ] Input validation (Zod/Yup schemas)
- [ ] CORS configuration
- [ ] CSRF protection for state-changing operations
- [ ] HTTPS everywhere

### Observability Stack
```
Logs → OpenTelemetry → Traces (Jaeger/Zipkin)
Metrics → Prometheus → Grafana
Errors → Sentry / LogRocket
```

---

## Quick Interview Checklist

- [ ] Can explain CAP theorem tradeoffs
- [ ] Know when to use SQL vs NoSQL vs Vector DB
- [ ] Can design a scalable API architecture
- [ ] Understand caching strategies at every layer
- [ ] Familiar with RAG pipeline components and evaluation
- [ ] Can describe multi-agent orchestration patterns
- [ ] Know how to handle distributed failures gracefully
