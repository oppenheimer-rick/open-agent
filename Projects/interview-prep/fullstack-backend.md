# Full-Stack & Backend Interview Prep — Shakib S.

## Tech Stack: Python, Node.js, FastAPI, Prisma, PostgreSQL/MySQL/MongoDB

---

## 1. API Design & REST Principles

### Core Concepts to Master

**RESTful Design:**
- Resource naming conventions (`/api/v1/users`, `/api/v1/users/:id`)
- HTTP methods: GET, POST, PUT, PATCH, DELETE — when to use which
- Status codes you'll be asked about: 200, 201, 204, 400, 401, 403, 404, 422, 429, 500
- Idempotency: Which methods are idempotent? (GET, PUT, DELETE)
- HATEOAS / HAL / JSON:API — know at least conceptually

**Common Interview Questions:**
1. "How do you version your APIs?" → v1/v2 path-based + backward compatibility strategy
2. "Design a paginated list endpoint" → cursor vs offset pagination, why cursor wins for large datasets
3. "How do you handle partial updates?" → PATCH vs PUT, JSON Merge Patch spec
4. "REST vs GraphQL vs gRPC?" → REST for CRUD APIs, GraphQL for flexible client queries, gRPC for internal microservices

---

## 2. FastAPI (Python) — Deep Dive

### Architecture & Patterns

**Request/Response Lifecycle:**
```
Client → Path Validation → Pydantic Model Parsing → Dependency Injection → Route Function → Response Serialization
```

**Key Concepts:**
- **Path Parameters** vs **Query Parameters** vs **Body Parameters**
- **Pydantic Models** for validation (schema-driven, type coercion)
- **Dependency Injection** (`Depends()`) — auth middlewares, DB sessions, rate limiters
- **BackgroundTasks** — fire-and-forget async jobs
- **WebSocket support** — real-time streaming

### Interview Questions

1. **"How does FastAPI handle async?"**
   - Uses `asyncio` event loop natively
   - `async def` for I/O-bound (DB calls, HTTP requests)
   - `def` + `run_in_executor` for CPU-bound tasks
   - Know the tradeoff: async doesn't help CPU-heavy work

2. **"How do you structure a large FastAPI app?"**
   - Use `APIRouter()` to split routes into modules
   - Centralize Pydantic models in `models/`
   - Dependency providers in `dependencies/`
   - Example structure:
     ```python
     # main.py
     from fastapi import FastAPI
     from routers import users, agents, payments
     
     app = FastAPI()
     app.include_router(users.router, prefix="/api/v1")
     app.include_router(agents.router, prefix="/api/v1")
     
     # dependencies/auth.py
     from fastapi import Depends, HTTPException
     from jose import jwt
     
     def get_current_user(token: str = Depends(oauth2_token)) -> User:
         payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
         return User.model_validate(payload)
     ```

3. **"How do you handle middleware in FastAPI?"**
   - `@app.middleware("http")` decorator for simple logging/auth
   - Starlette's `CoroMiddleware` for complex pipelines
   - Auth flow: extract token → validate → inject into request context

4. **"Streaming responses in FastAPI"** (your Local LLM Web UI project!)
   ```python
   from fastapi import Response
   from asyncio import AsyncGenerator
   
   async def event_generator():
       async for token in llm_stream(prompt):
           yield f"data: {token}\n\n"
   
   @app.get("/stream")
   async def stream_response():
       return Response(event_generator(), media_type="text/event-stream")
   ```

---

## 3. Node.js / Express

### Architecture & Patterns

**Request/Response Lifecycle:**
```
Client → URL Match → Middleware Stack → Route Handler → Send Response
```

**Key Concepts:**
- **Middleware chain** — order matters, `next()` is critical
- **Error handling middleware** — single `(err, req, res, next)` signature
- **Request object lifecycle** — `req.body`, `req.query`, `req.params`
- **Streams** for large file/DB responses

### Interview Questions

1. **"How do you handle errors in Express?"**
   - Try/catch in async route handlers (Express 4+ auto-catches)
   - Centralized error middleware at the end of stack
   - Custom error classes (`class AppError extends Error`)
   
2. **"How do you structure a large Node.js app?"**
   - MVC pattern or feature-based grouping
   - Route files → Controller logic → Service layer → DB layer
   - Example: `src/routes/auth.ts`, `src/controllers/authController.ts`, `src/services/authService.ts`

3. **"Express vs FastAPI for the same use case?"**
   - Express: more flexible, JS ecosystem, callback-heavy historically (now async/await)
   - FastAPI: built-in validation, auto-docs, native async, type safety via Pydantic
   - Tradeoff: FastAPI dev speed + correctness vs Express ecosystem maturity

---

## 4. ORM & Database — Prisma, SQLAlchemy, Mongoose

### Prisma (Node.js/TypeScript)

**Key Concepts:**
- Schema-first approach (`schema.prisma`)
- Generated TypeScript types from schema
- Lazy loading vs eager loading relations
- `$transaction()` for atomic multi-table operations

**Interview Questions:**
1. **"What's an N+1 query problem and how do you fix it?"**
   - Fetching users → then fetching each user's posts separately = N queries
   - Fix: `include: { posts: true }` in Prisma, or JOIN in raw SQL

2. **"How do you handle migrations in production?"**
   - `prisma migrate deploy` (production) vs `prisma migrate dev` (dev)
   - Always test migrations on staging first
   - Rollback strategy: keep migration files immutable, never edit applied ones

### SQLAlchemy (Python/FastAPI)

**Key Concepts:**
- Session management (`SessionLocal`, dependency injection)
- Relationship loading strategies (joinedload, subqueryload)
- Alembic for migrations

### Common Interview Questions Across ORMs:

1. **"When would you use raw SQL vs ORM?"**
   - ORM: CRUD, joins, transactions — 80% of cases
   - Raw SQL: complex analytics, window functions, full-text search, performance-critical queries

2. **"How do you handle database connections in async apps?"**
   - Connection pooling (PgBouncer, SQLAlchemy pool)
   - Never store connections in global/state
   - Use dependency injection per-request

---

## 5. Async Systems & Streaming UX

### Your Edge — Production Experience

**Streaming LLM Responses:**
- SSE (Server-Sent Events) for frontend → backend streaming
- WebSocket for bidirectional real-time (chat interfaces)
- Buffering strategy: don't wait for full response, stream token-by-token

**Your Local LLM Web UI Example:**
```python
# FastAPI async generator for streaming
async def chat_stream(prompt: str):
    async for chunk in ollama_client.chat_stream(model="qwen2.5", messages=[{"role": "user", "content": prompt}]):
        yield json.dumps({"token": chunk["message"]["content"]})

@app.get("/chat/stream")
async def stream_chat(prompt: str = Query()):
    return StreamingResponse(
        chat_stream(prompt),
        media_type="application/x-ndjson"
    )
```

**Interview Questions:**
1. **"How do you handle slow upstream services?"** → timeouts, circuit breakers, retry with exponential backoff
2. **"How do you stream to React?"** → SSE or WebSocket, `EventSource` API, loading states, error boundaries
3. **"What's the difference between streaming and chunking?"** → Streaming = real-time tokens, Chunking = pre-split document for RAG

---

## 6. Production Guardrails (Your Differentiator)

### Cost Controls & Observability
- **Rate limiting**: token/minute budgets per user
- **Fallback models**: if GPT-4 fails, fallback to cheaper model
- **Output validation**: regex/prompt-based quality checks before returning to user
- **Caching**: Redis for repeated prompts (cost + latency savings)

### Interview Questions:
1. **"How do you ensure LLM output quality?"** → Schema validation, confidence scoring, human-in-the-loop fallback
2. **"How do you handle LLM failures in production?"** → Retry with exponential backoff, circuit breaker pattern, graceful degradation
3. **"How do you monitor an AI pipeline?"** → Trace spans per request, token usage logs, latency p50/p95, error rates

---

## 7. Database Optimization

### PostgreSQL / MySQL

**Indexing:**
- B-tree (default) vs Hash vs GIN (JSONB) vs BRIN (time-series)
- When to add indexes: WHERE clauses, JOIN conditions, ORDER BY
- Tradeoff: faster reads, slower writes, more disk space

**Query Optimization:**
- `EXPLAIN ANALYZE` to read query plans
- Avoid `SELECT *` — select only needed columns
- Pagination: use cursor-based for large datasets (offset pagination gets worse as page increases)

### MongoDB

**When to use MongoDB vs PostgreSQL?**
- MongoDB: document-heavy, schema-less, geospatial queries, high write throughput
- PostgreSQL: relational integrity, complex joins, ACID transactions

---

## 8. Security & Auth Patterns

**JWT Flow:**
```python
# FastAPI example
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")

def decode_token(token: str = Depends(oauth2_scheme)) -> dict:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        return payload
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
```

**Common Questions:**
1. "How do you store passwords?" → bcrypt/scrypt/argon2 hashing, NEVER plain text
2. "JWT vs Session cookies?" → JWT = stateless, good for microservices; Sessions = server-side state, revocable
3. "How to prevent CSRF?" → SameSite cookie attribute, CSRF tokens for form submissions

---

## 9. Testing & CI/CD

**FastAPI Testing:**
```python
from fastapi.testclient import TestClient

def test_read_main():
    with TestClient(app) as client:
        response = client.get("/api/v1/users")
        assert response.status_code == 200
        assert "users" in response.json()
```

**Interview Questions:**
1. "How do you test async code?" → `pytest-asyncio`, TestClient for integration tests
2. "Unit vs Integration vs E2E?" → Unit (pure logic), Integration (DB + API), E2E (full flow)
3. "CI/CD pipeline structure?" → lint → test → build → deploy (GitHub Actions, Docker, Kubernetes)

---

## 10. Scalability Patterns

**Horizontal Scaling:**
- Stateless API servers behind load balancer (nginx, AWS ALB)
- Connection pooling (PgBouncer for PostgreSQL)
- Redis for distributed caching

**Vertical Scaling:**
- Optimize slow queries first
- Add indexes before adding more servers

**Your Multi-Agent System — Scale Considerations:**
- Queue-based agent orchestration (RabbitMQ, Celery, Bull)
- Rate limiting per tenant/user
- Circuit breakers between agents

---

## Quick Interview Checklist

- [ ] Can explain REST + status codes + pagination
- [ ] Can write FastAPI route with Pydantic + dependency injection
- [ ] Can explain ORM N+1 problem and fix it
- [ ] Can describe async vs sync in Python/Node.js
- [ ] Can design a streaming chat UI backend
- [ ] Can talk through auth flow (JWT, OAuth)
- [ ] Know when to use SQL vs NoSQL
- [ ] Can explain production guardrails for AI systems
- [ ] Have 2-3 war stories from Maniac Esports / Local LLM Web UI
