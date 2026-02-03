# Scaling Guide

This document outlines scaling thresholds and migration paths for My Poker Face.

---

## Current Architecture Constraints

| Component | Current State | Limitation |
|-----------|---------------|------------|
| Database | SQLite with WAL mode | Single writer, file-based |
| Web server | Single Gunicorn worker | Thread pool limits concurrency |
| Game state | In-memory dict | Single process, no sharing |
| SocketIO | Threading async mode | Can't scale workers without Redis |
| LLM calls | Synchronous | Blocks threads during decisions |

---

## Scaling Stages

| Stage | Concurrent Games | What's Needed |
|-------|------------------|---------------|
| **Current** | 1-10 | Works as-is |
| **Growing** | 10-30 | Monitor SQLite locks, memory, LLM costs |
| **Scaling** | 30-100 | Multi-worker setup (T3-40), Redis for SocketIO |
| **Large** | 100-200 | PostgreSQL, optimized state updates |
| **Horizontal** | 200+ | Load balancer, multiple servers |

---

## Database: SQLite → PostgreSQL

### When to Migrate

| Trigger | Threshold | Why |
|---------|-----------|-----|
| Concurrent writes | ~50-100 concurrent games | SQLite allows only 1 writer at a time |
| Database size | ~10-50 GB | Performance degrades with very large files |
| Multi-server deployment | Any | SQLite is file-based, can't share across servers |
| Write-heavy analytics | Heavy experiment runs | Experiments write extensively to api_usage, prompt_captures, decision_analysis |

### Early Warning Signs

- `SQLITE_BUSY` errors in logs
- Increasing write latency
- Database lock timeouts during experiments
- Slow queries on large tables (prompt_captures, api_usage)

### Migration Complexity

**Medium-high effort**:
- 30+ tables defined in `schema_manager.py`
- 63 migration methods (most are no-ops)
- Would need Alembic or similar migration tool
- Test thoroughly — hand evaluation edge cases are critical

### Migration Steps

1. Set up PostgreSQL instance
2. Install `psycopg2` or `asyncpg`
3. Create Alembic migration environment
4. Port schema from `schema_manager.py`
5. Write data migration script
6. Update `BaseRepository` connection handling
7. Test extensively (especially hand evaluation, experiments)
8. Blue-green deployment with rollback plan

---

## Server Capacity: Single → Multi-Worker

### Current Bottlenecks

| Resource | Limit | Bottleneck |
|----------|-------|------------|
| WebSocket connections | ~100-200 per worker | Each game = 1-6 connections |
| Thread pool | ~10-20 threads default | AI decisions block threads (~2-5s each) |
| Memory | ~50-100MB per active game | Game state + messages + AI controller state |
| CPU | Moderate | AI orchestration, not LLM inference |

### The Math

- 30 games × 4 AI players avg × 5s decision time = 600s of potential blocking work
- With 10 threads, max ~10 concurrent AI decisions
- Games waiting for AI = degraded UX (spinning, delays)

### Multi-Worker Path (T3-40)

1. **Add Redis** for SocketIO message queue:
   ```python
   socketio = SocketIO(message_queue='redis://localhost:6379')
   ```

2. **Fix async_mode** for multi-worker compatibility

3. **Bump workers** (2-4 per CPU core):
   ```bash
   gunicorn -w 4 -k geventwebsocket.gunicorn.workers.GeventWebSocketWorker
   ```

4. **Audit thread safety** in `game_state_service.py`

5. **Review locking** in `progress_game()` — per-game locks work, but verify no deadlocks

### When Multi-Worker Isn't Enough

Signs you've outgrown a single server:
- CPU sustained at 80%+ even with multiple workers
- Memory fully utilized (can't add more workers)
- Want high availability (single server = SPOF)
- Need zero-downtime deployments

---

## Horizontal Scaling: Load Balancer + Multiple Servers

### When to Add a Second Server

| Trigger | Threshold | Notes |
|---------|-----------|-------|
| CPU saturation | 80%+ sustained on single server | Can't add more workers |
| Memory ceiling | Server RAM fully utilized | Game state + worker memory overhead |
| High availability | Business requirement | Single server = single point of failure |
| Zero-downtime deploys | User expectation | Rolling deploys need 2+ servers |
| Geographic latency | Global user base | Edge servers reduce RTT |

### Estimated Capacity

A single **4-8 core server with 16-32GB RAM** running multi-worker setup can likely handle:
- **100-200 concurrent games** before needing horizontal scaling
- Bottleneck is usually LLM API latency, not server resources

### Prerequisites

**Must complete T3-40 first** — without Redis-backed SocketIO and stateless workers, horizontal scaling requires complex sticky sessions.

### Horizontal Architecture

```
                    ┌─────────────────┐
                    │  Load Balancer  │
                    │  (nginx/HAProxy)│
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
        ┌──────────┐  ┌──────────┐  ┌──────────┐
        │ Server 1 │  │ Server 2 │  │ Server N │
        │ Workers  │  │ Workers  │  │ Workers  │
        └────┬─────┘  └────┬─────┘  └────┬─────┘
             │             │             │
             └─────────────┼─────────────┘
                           ▼
              ┌─────────────────────────┐
              │         Redis           │
              │  (SocketIO pub/sub +    │
              │   session store)        │
              └───────────┬─────────────┘
                          │
                          ▼
              ┌─────────────────────────┐
              │      PostgreSQL         │
              │   (shared database)     │
              └─────────────────────────┘
```

### Load Balancer Configuration

For WebSocket support:
```nginx
upstream poker_backend {
    ip_hash;  # Sticky sessions until Redis-backed
    server server1:5001;
    server server2:5001;
}

server {
    location /socket.io/ {
        proxy_pass http://poker_backend;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

**Note**: `ip_hash` provides sticky sessions. Once SocketIO uses Redis pub/sub, can switch to round-robin for better distribution.

---

## LLM API Considerations

### Cost & Rate Limits

| Call Type | Frequency | Cost Risk |
|-----------|-----------|-----------|
| Player decisions | Every AI turn (~4-8 per hand) | Highest volume |
| Commentary | End of each hand | Medium |
| Avatar generation | Once per personality | Low (cached) |
| Experiments | Bulk runs (1000s of hands) | Can spike quickly |

### Rate Limit Math

- OpenAI: 10,000 RPM on most tiers
- 100 concurrent games × 6 AI decisions/min = 600 RPM (safe margin)
- Experiments can easily hit 1000s of calls/hour

### Mitigations

- **UsageTracker** already monitors all calls
- Consider response caching for similar game states
- Rate limit experiment execution (already has pause/resume)
- Use faster/cheaper models for non-critical calls (Fast tier)

---

## Memory Management

### Current Protections

| Component | Protection | Reference |
|-----------|------------|-----------|
| Game state cache | 2-hour TTL with auto-cleanup | T2-19 (fixed) |
| Messages per game | Capped at 200 entries | T2-20 (fixed) |
| AI conversation history | Cleared each turn | By design |
| Opponent models | Small per player-pair | Unbounded but minimal |

### Monitoring

Watch for:
- Total process memory growth over time
- Game count in `game_state_service.games`
- Long-running games accumulating state

---

## WebSocket Optimization

### Current Pattern

Every action broadcasts full `game_state` (~10-50KB) to all room members.

### When It Hurts

- Large spectator counts (50+ watching one game)
- High-frequency updates (run-it-out mode deals cards rapidly)
- Mobile clients on slow connections

### Future Optimization: Delta Updates

Instead of full state:
```python
# Current
socketio.emit('update_game_state', {'game_state': full_state}, to=game_id)

# Optimized
socketio.emit('state_delta', {
    'pot': new_pot,
    'current_player_idx': new_idx,
    'player_updates': [{'idx': 2, 'stack': 1500, 'bet': 100}]
}, to=game_id)
```

Requires frontend state reconciliation logic.

---

## Monitoring Checklist

### Pre-Scaling (Current)

- [ ] Monitor SQLite write latency via logs
- [ ] Track `api_usage` table for LLM costs
- [ ] Watch server memory usage
- [ ] Count active games in logs

### Growth Phase (10-30 games)

- [ ] Set up proper APM (DataDog, New Relic, or self-hosted)
- [ ] Alert on `SQLITE_BUSY` errors
- [ ] Alert on memory > 80%
- [ ] Track p95 response times

### Scale Phase (30-100 games)

- [ ] Redis metrics (connections, memory, pub/sub lag)
- [ ] Per-worker metrics
- [ ] Database connection pool stats
- [ ] LLM API rate limit proximity

---

## Summary

| Milestone | Games | Key Actions |
|-----------|-------|-------------|
| **Now** | 1-10 | Monitor, optimize queries |
| **Soon** | 10-30 | Prepare Redis, plan multi-worker |
| **Growth** | 30-100 | Deploy multi-worker (T3-40) |
| **Scale** | 100-200 | PostgreSQL migration |
| **Expand** | 200+ | Load balancer, multiple servers |

The architecture is sound for early growth. Main work is T3-40 (multi-worker) which unlocks the path to horizontal scaling.
