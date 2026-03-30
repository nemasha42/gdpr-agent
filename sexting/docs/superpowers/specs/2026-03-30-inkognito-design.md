# Inkognito — Anonymous Chat with Curated Erotic Content Library

**Date:** 2026-03-30
**Status:** Draft
**Platform:** Telegram Mini App (Phase 1) → Standalone App (Phase 2)

## Overview

Inkognito is an anonymous chat app where users pick a nickname, set preferences, and get matched with strangers for ephemeral conversations. The app includes a curated library of artist-drawn erotic stickers and GIFs. Artists earn through user tips via Telegram Stars. All content is illustrated (not photographic). Privacy is a-la-carte — users choose which privacy features to enable.

## Core User Flow

1. **Open Mini App** — user taps bot link or `/start` in Telegram. Backend validates Telegram `initData`.
2. **Age Gate** — one-time "I am 18+" confirmation. Blocks all features until confirmed.
3. **Create Profile** — pick a nickname (unique, changeable), optional avatar (preset library or upload), gender identity (male/female/non-binary/prefer not to say), interested in (male/female/everyone/random).
4. **Home Screen** — four connection modes:
   - **Random Match** — pick a category, toggle available, get paired
   - **Invite Link** — generate a link, share with a specific person
   - **Discover** — browse available users by category/interest
   - **Group Chat** — create or join a themed room
5. **Matching** — system pairs two available users with compatible preferences.
6. **Anonymous Chat** — both users see only nicknames. Built-in sticker/GIF library. Messages exist only while chat is live.
7. **Post-Chat** — "Add to favorites" (mutual favorites get notified when online), "Find another match", or leave.

## Architecture

```
Telegram App
    ↓
Mini App (React SPA)
    ↓ HTTPS + WebSocket
Backend API (Node.js / Fastify)
    ↓           ↓           ↓
PostgreSQL    Redis      S3 / CDN
    ↕
Telegram Bot API (auth, Stars payments, notifications)
```

- **React SPA** — chat UI, sticker browser, profile, matching. Runs inside Telegram's Mini App container.
- **Fastify backend** — HTTP API for profiles/content/tipping + WebSocket for real-time chat and matching.
- **PostgreSQL** — persistent data: users, artists, sticker packs, tips, contacts.
- **Redis** — ephemeral data: matching queues, active chat sessions, messages (memory only), online status, WebSocket routing.
- **S3 + CDN** — sticker and GIF asset storage and delivery (Cloudflare R2 for free egress).
- **Telegram Bot API** — auth via `initData` validation, Stars payments for tipping, bot notifications.

Chat messages are never persisted to disk. They exist in Redis during the active session and are purged when the chat ends.

## Privacy Features

All toggleable per-user. Defaults chosen for a balanced experience — user adjusts to their comfort level.

| Feature | Default | Description |
|---------|---------|-------------|
| Disappearing messages | ON | Messages purged when chat ends. Optional per-message timer: 5s / 30s / 5min after read. |
| Screenshot detection | OFF | Alerts the other person when a screenshot is taken (via Telegram WebApp API). Cannot block, only notify. |
| Hidden chat history | OFF | Saved contact chats hidden behind PIN or biometric unlock. |
| Typing indicator | ON | Shows "typing..." to the other person. |
| Read receipts | ON | Blue checkmarks when message is read. |
| Online status | ON | Shows "online" / "last seen" to contacts. |

### Anonymity Guarantees

- Telegram user ID is never exposed to other users. Server maps Telegram ID to internal UUID on auth. All chat traffic uses UUIDs only.
- Nickname is the only identity visible. No phone number, Telegram username, or profile photo leaks.
- Server-side enforcement — even WebSocket frame inspection reveals only UUIDs.
- No chat logs by default — messages held in Redis (memory only) during active chat.
- Metadata minimization — no IP logging, no device fingerprinting. Connection logs rotated every 24h.

## Chat Features

- **Text messages** — real-time via WebSocket. Markdown support (bold/italic).
- **Sticker picker** — bottom panel, browse by pack, recently used tab, tap to send.
- **GIF browser** — search + browse curated library, categories match chat categories.
- **Photo/video sharing** — optional toggle per-chat. Requires consent: when one user tries to send a photo, the other gets an "Accept?" prompt before anything is transmitted. View-once option available.
- **Voice messages** — hold to record, release to send. Follows disappearing message rules.
- **Tip artist** — long-press any sticker/GIF → "Tip the artist" → Telegram Stars payment.
- **Block user** — available during or after chat. Blocked nicknames are never matched again. Mutual — blocks both directions.
- **Report** — single "Report" button in chat menu. Reviewed on incoming basis only.

## Matching System

### Algorithm

Queue-based matching using Redis sorted sets, one per category.

1. User selects a category (e.g. "Flirty", "Roleplay", "Casual", "Spicy") and toggles "Available".
2. User enters the matching queue (Redis sorted set, scored by timestamp — longest-waiting matched first).
3. Server continuously scans queues. For each candidate pair, checks:
   - Mutual gender compatibility (A wants B's gender AND B wants A's gender)
   - Not in cooldown (anti-repeat: recently matched pairs blocked for 1 hour)
   - Neither user has blocked the other
4. Match found → both users receive "Match found!" → WebSocket chat session created.
5. No match within 60s → "Still looking..." prompt with option to broaden category or switch to "Random".

### Rate Limiting

- Match attempts: max 20 per hour per user
- Messages: max 30 per minute per user per chat session
- Tip frequency: max 10 tips per hour per user
- Report: max 5 per day per user

### Connection Modes

- **Random Match** — queue-based matching as described above.
- **Invite Link** — generates a unique link. When the invitee opens it, a direct chat session starts. Both anonymous (nicknames only).
- **Discover** — browse users who have opted into discovery. Shows nickname, avatar, category interest. Tap to send a chat request; other person accepts or ignores.
- **Group Chat** — themed rooms. Creator picks name + category, sets max participants (5-50). Same anonymity rules. Room creator can kick users. Ephemeral by default — room dies when empty for 30 minutes or creator closes it. Public rooms appear on the Discover tab with participant count. Group messages follow the same ephemeral rules as 1-on-1 chats (Redis only, purged when room closes).

## Favorites & Contacts

- After a chat ends, either user can "Add to favorites".
- **Mutual favorites** — when both users favorite each other, they get notified when the other comes online. This is the primary retention hook.
- **Chat request** — saved contacts can send a "Want to chat?" ping. The other person accepts or ignores. No way to message without consent.
- Contacts store nickname snapshot at time of save. If user changes nickname, contacts see the updated one.

## Content Marketplace

### User Experience

- Sticker/GIF browser accessible from any chat via bottom panel.
- Browse by category: Flirty, Spicy, Romantic, Playful, Emoji.
- Search across all packs.
- "Add to favorites" per pack for quick access.
- Long-press sticker in chat → "Tip ⭐" → preset amounts (1, 5, 10, 50 Stars) or custom.
- Artist profile page with "Support this artist ⭐" button.
- Quick "⭐ Tip" floating button appears briefly after sending a sticker (non-intrusive, auto-dismisses).

### Artist Dashboard

- Earnings overview: total tips, this month, available to withdraw.
- Per-pack stats: sticker count, use count, tip count.
- Upload interface with requirements:
  - Stickers: WebP or PNG, 512x512px
  - GIFs: MP4 or WebM, max 3s, 512x512px
  - Minimum 8 per pack, maximum 120
  - Pack name + thumbnail required
  - Category tag required
- Content guidelines: adults only, fictional characters only, no depictions of minors.

### Artist Onboarding Phases

**Phase 1 — Curated (launch):** You invite artists directly. Review portfolio before approval. Manual upload via admin panel. Target: 10-20 artists, 30-50 packs at launch.

**Phase 2 — Application:** Artists apply via form, submit sample pack for review. Approved artists get self-serve upload access. Quality threshold maintained by existing artist votes.

**Phase 3 — Open:** Anyone can upload. Community ratings filter quality. Top artists get "Featured" badge. Automated content guidelines check.

### Tipping Economics

- Payment: Telegram Stars API — user pays through Telegram's native payment flow.
- Artists receive 100% of tips (no platform cut — revisit at monetization phase).
- Withdrawal: artists convert Stars via Telegram's built-in mechanisms.

## Data Model

### PostgreSQL Tables

**users** — `id` (uuid PK), `telegram_id` (bigint, unique, encrypted), `nickname` (varchar 30, unique), `avatar_url` (nullable), `gender` (enum), `interested_in` (enum), `age_confirmed` (boolean), `discoverable` (bool, default false — opt-in to Discover browse), `created_at`, `last_seen_at`

**privacy_settings** — `user_id` (FK → users), `disappearing_messages` (bool, default true), `disappear_timer_sec` (int, nullable), `screenshot_detection` (bool, default false), `hidden_chats_pin` (varchar, nullable, hashed), `typing_indicator` (bool, default true), `read_receipts` (bool, default true), `show_online_status` (bool, default true)

**artists** — `id` (uuid PK), `user_id` (FK → users), `display_name` (varchar 40), `bio` (text), `status` (enum: invited/active/suspended), `total_tips_stars` (bigint), `approved_at`

**sticker_packs** — `id` (uuid PK), `artist_id` (FK → artists), `name` (varchar 60), `category` (enum), `thumbnail_url`, `sticker_count` (int), `use_count` (bigint, denormalized), `tip_count` (bigint, denormalized), `is_featured` (bool), `created_at`

**stickers** — `id` (uuid PK), `pack_id` (FK → sticker_packs), `file_url` (CDN URL), `file_type` (enum: webp/png/mp4/webm), `is_animated` (bool), `sort_order` (int)

**tips** — `id` (uuid PK), `from_user_id` (FK), `to_artist_id` (FK), `sticker_pack_id` (FK, nullable), `amount_stars` (int), `telegram_payment_id` (text), `created_at`

**chat_sessions** — `id` (uuid PK), `user_a_id`, `user_b_id`, `category` (varchar), `match_type` (enum: random/invite/discover), `started_at`, `ended_at` (nullable). Row deleted 24h after `ended_at`.

**contacts** — `id` (uuid PK), `owner_id` (FK), `contact_user_id` (FK), `contact_nickname` (varchar, snapshot), `is_hidden` (bool, default false), `is_favorite` (bool, default false), `is_mutual_favorite` (bool, computed), `created_at`

**blocks** — `blocker_id` (FK → users), `blocked_id` (FK → users), `created_at`. Unique constraint on (blocker_id, blocked_id).

**reports** — `id` (uuid PK), `reporter_id` (FK), `reported_user_id` (FK), `chat_session_id` (FK, nullable), `reason` (text), `created_at`

### Redis Keys

| Key | Type | Purpose |
|-----|------|---------|
| `matching:{category}` | Sorted Set | Matching queue, scored by timestamp |
| `session:{chat_id}` | Hash | Active chat metadata (user_a, user_b, started_at) |
| `messages:{chat_id}` | List | Message objects (purged on chat end) |
| `online:{user_id}` | Key + TTL | Online status (heartbeat every 30s) |
| `ws:{user_id}` | String | Maps user to WebSocket server node |
| `cooldown:{userA}:{userB}` | Key + 1h TTL | Anti-repeat matching |
| `ratelimit:{user_id}:{action}` | Counter + TTL | Rate limiting per action type |

## Tech Stack

### Frontend
- React 19 + TypeScript
- Vite (bundler)
- @telegram-apps/sdk (Mini App SDK)
- TanStack Query (data fetching/caching)
- Zustand (state management)
- Tailwind CSS (styling)
- Native WebSocket (chat connection)

### Backend
- Node.js 22 + TypeScript
- Fastify (HTTP + WebSocket via @fastify/websocket)
- Prisma (ORM for PostgreSQL)
- ioredis (Redis client)
- grammy (Telegram Bot API)
- zod (input validation)
- pino (structured logging)

### Infrastructure
- PostgreSQL 16
- Redis 7 (no persistence — ephemeral data only)
- Cloudflare R2 (S3-compatible, free egress for sticker/GIF CDN)
- Cloudflare CDN (global asset delivery)
- Docker Compose (local development)

### Deployment (Phase 1)
- Railway or Fly.io — backend + PostgreSQL + Redis
- Cloudflare Pages — static frontend hosting
- Cloudflare R2 — asset storage
- Estimated cost: ~$15-30/month at launch scale

## Moderation (Minimum Viable)

- **Age gate** — "I am 18+" on first launch. One-time, stored in profile.
- **Report button** — in chat menu. Reports reviewed on incoming basis.
- **Block** — immediate, prevents future matching.
- **ToS** — "consenting adults only, fictional illustrated content only, no depictions of minors, no illegal content."
- **Artist content guidelines** — enforced at upload review (Phase 1: manual by you; Phase 2+: community + automated).

No proactive chat scanning. No AI moderation of conversations. Content library is curated illustrated art — no photographic CSAM risk.

## Phase 2: Standalone App (Future)

When Telegram Mini App validates the concept:
- Same React frontend, wrapped in Capacitor or React Native shell
- Same backend — swap Telegram auth for email/phone auth
- Add push notifications (FCM/APNs)
- Add own payment processing (Stripe) alongside Stars
- Consider E2E encryption
- App store submission (requires age verification upgrade for Apple/Google)

## Future Backlog

Items discussed but deferred past Phase 1:

- Tip leaderboard ("Top tipped artists this week") for social proof and artist marketing
- Artist referral program (existing artists invite new ones, bonus on first tip)
- Cooldown after report (rate-limit reported users from matching while review pending)
- Nickname squatting policy (release inactive nicknames after 90 days)
- Video/voice calls
- AI-generated stickers
- End-to-end encryption
- Push notifications (Telegram handles in Phase 1)
