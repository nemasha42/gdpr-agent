# Inkognito Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a working Telegram Mini App with authenticated users, profiles, privacy settings, and age gate — the foundation for all subsequent plans.

**Architecture:** Monorepo with `server/` (Fastify + Prisma + ioredis) and `web/` (React + Vite + Telegram SDK). Docker Compose runs PostgreSQL and Redis locally. Telegram Bot validates `initData` for auth, issues JWTs for session management.

**Tech Stack:** Node.js 22, TypeScript, Fastify, Prisma, ioredis, grammy, zod, pino (backend); React 19, Vite, @telegram-apps/sdk, TanStack Query, Zustand, Tailwind CSS (frontend).

**Spec:** `docs/superpowers/specs/2026-03-30-inkognito-design.md`

**Subsequent plans:**
- Plan 2: Matching & Chat (WebSocket, Redis matching, real-time messaging)
- Plan 3: Content & Tipping (sticker library, artist platform, Telegram Stars)
- Plan 4: Social & Groups (contacts, favorites, invite links, discover, group chat)

---

## File Structure

```
inkognito/
├── docker-compose.yml              # PostgreSQL 16 + Redis 7
├── package.json                    # Workspace root
├── .env.example                    # Environment variable template
├── server/
│   ├── package.json
│   ├── tsconfig.json
│   ├── prisma/
│   │   └── schema.prisma           # Full DB schema (all tables from spec)
│   ├── src/
│   │   ├── index.ts                # Fastify server entry point
│   │   ├── config.ts               # Environment config with zod validation
│   │   ├── lib/
│   │   │   ├── prisma.ts           # Prisma client singleton
│   │   │   ├── redis.ts            # ioredis client singleton
│   │   │   └── telegram.ts         # initData validation + bot setup (grammy)
│   │   ├── routes/
│   │   │   ├── auth.ts             # POST /auth/telegram — validate initData, return JWT
│   │   │   ├── users.ts            # GET/PUT /users/me, POST /users/me/age-confirm
│   │   │   └── privacy.ts          # GET/PUT /users/me/privacy
│   │   ├── middleware/
│   │   │   └── auth.ts             # JWT verification middleware
│   │   └── utils/
│   │       └── crypto.ts           # Telegram ID encryption/decryption
│   └── tests/
│       ├── helpers/
│       │   └── setup.ts            # Test DB setup, fixtures, cleanup
│       ├── lib/
│       │   └── telegram.test.ts    # initData validation tests
│       ├── routes/
│       │   ├── auth.test.ts        # Auth route tests
│       │   ├── users.test.ts       # User profile route tests
│       │   └── privacy.test.ts     # Privacy settings route tests
│       └── utils/
│           └── crypto.test.ts      # Encryption tests
├── web/
│   ├── package.json
│   ├── tsconfig.json
│   ├── vite.config.ts
│   ├── index.html
│   ├── src/
│   │   ├── main.tsx                # React entry point + Telegram SDK init
│   │   ├── App.tsx                 # Router + auth guard
│   │   ├── api/
│   │   │   └── client.ts           # Fetch wrapper with JWT
│   │   ├── stores/
│   │   │   └── auth.ts             # Zustand auth store
│   │   ├── hooks/
│   │   │   ├── useAuth.ts          # Auth hook (login, token refresh)
│   │   │   └── useProfile.ts       # Profile CRUD hook (TanStack Query)
│   │   ├── pages/
│   │   │   ├── AgeGate.tsx         # Age confirmation screen
│   │   │   ├── CreateProfile.tsx   # Nickname, avatar, gender, preferences
│   │   │   ├── Home.tsx            # Home screen (placeholder for Plan 2)
│   │   │   └── Settings.tsx        # Privacy settings toggles
│   │   └── components/
│   │       ├── Layout.tsx          # App shell + bottom nav
│   │       └── AvatarPicker.tsx    # Preset avatar grid
│   └── tests/
│       └── (frontend tests deferred — backend-first in this plan)
└── .gitignore
```

---

### Task 1: Project Scaffolding & Docker Compose

**Files:**
- Create: `inkognito/package.json`
- Create: `inkognito/docker-compose.yml`
- Create: `inkognito/.env.example`
- Create: `inkognito/.gitignore`

- [ ] **Step 1: Create monorepo root**

```bash
mkdir -p /Users/nemasha/MyProjects/gdpr-agent/sexting/inkognito
cd /Users/nemasha/MyProjects/gdpr-agent/sexting/inkognito
```

```json
// package.json
{
  "name": "inkognito",
  "private": true,
  "workspaces": ["server", "web"]
}
```

- [ ] **Step 2: Create Docker Compose**

```yaml
# docker-compose.yml
services:
  postgres:
    image: postgres:16-alpine
    ports:
      - "5432:5432"
    environment:
      POSTGRES_USER: inkognito
      POSTGRES_PASSWORD: inkognito_dev
      POSTGRES_DB: inkognito
    volumes:
      - pgdata:/var/lib/postgresql/data

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    command: redis-server --save "" --appendonly no

volumes:
  pgdata:
```

- [ ] **Step 3: Create .env.example and .gitignore**

```bash
# .env.example
DATABASE_URL=postgresql://inkognito:inkognito_dev@localhost:5432/inkognito
REDIS_URL=redis://localhost:6379
BOT_TOKEN=your_telegram_bot_token
JWT_SECRET=change_me_in_production
TELEGRAM_ID_ENCRYPTION_KEY=32_byte_hex_key_here
```

```gitignore
# .gitignore
node_modules/
dist/
.env
*.log
.DS_Store
```

- [ ] **Step 4: Start Docker services and verify**

Run: `cd /Users/nemasha/MyProjects/gdpr-agent/sexting/inkognito && docker compose up -d`
Expected: Both containers start. Verify with `docker compose ps` — both `postgres` and `redis` show "running".

Run: `docker compose exec postgres pg_isready`
Expected: `localhost:5432 - accepting connections`

Run: `docker compose exec redis redis-cli ping`
Expected: `PONG`

- [ ] **Step 5: Commit**

```bash
git add inkognito/
git commit -m "chore: scaffold inkognito monorepo with Docker Compose (PostgreSQL + Redis)"
```

---

### Task 2: Server Package & Configuration

**Files:**
- Create: `inkognito/server/package.json`
- Create: `inkognito/server/tsconfig.json`
- Create: `inkognito/server/src/config.ts`

- [ ] **Step 1: Initialize server package**

```json
// server/package.json
{
  "name": "@inkognito/server",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "tsx watch src/index.ts",
    "build": "tsc",
    "test": "vitest run",
    "test:watch": "vitest"
  },
  "dependencies": {
    "@fastify/cors": "^10.0.0",
    "@fastify/websocket": "^11.0.0",
    "@prisma/client": "^6.4.0",
    "fastify": "^5.2.0",
    "grammy": "^1.35.0",
    "ioredis": "^5.6.0",
    "jose": "^6.0.0",
    "pino": "^9.6.0",
    "zod": "^3.24.0"
  },
  "devDependencies": {
    "@types/node": "^22.0.0",
    "prisma": "^6.4.0",
    "tsx": "^4.19.0",
    "typescript": "^5.7.0",
    "vitest": "^3.0.0"
  }
}
```

```json
// server/tsconfig.json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "bundler",
    "esModuleInterop": true,
    "strict": true,
    "outDir": "dist",
    "rootDir": "src",
    "declaration": true,
    "skipLibCheck": true,
    "forceConsistentCasingInFileNames": true
  },
  "include": ["src"],
  "exclude": ["node_modules", "dist", "tests"]
}
```

- [ ] **Step 2: Write config with zod validation**

```typescript
// server/src/config.ts
import { z } from "zod";

const envSchema = z.object({
  DATABASE_URL: z.string().url(),
  REDIS_URL: z.string().url(),
  BOT_TOKEN: z.string().min(1),
  JWT_SECRET: z.string().min(16),
  TELEGRAM_ID_ENCRYPTION_KEY: z.string().length(64, "Must be 32-byte hex key"),
  PORT: z.coerce.number().default(3000),
  NODE_ENV: z.enum(["development", "production", "test"]).default("development"),
});

export type Config = z.infer<typeof envSchema>;

export function loadConfig(): Config {
  const result = envSchema.safeParse(process.env);
  if (!result.success) {
    const missing = result.error.issues.map((i) => i.path.join(".")).join(", ");
    throw new Error(`Invalid environment: ${missing}`);
  }
  return result.data;
}
```

- [ ] **Step 3: Install dependencies**

Run: `cd /Users/nemasha/MyProjects/gdpr-agent/sexting/inkognito/server && npm install`
Expected: Installs without errors.

- [ ] **Step 4: Verify TypeScript compiles config**

Run: `cd /Users/nemasha/MyProjects/gdpr-agent/sexting/inkognito/server && npx tsc --noEmit src/config.ts`
Expected: No errors.

- [ ] **Step 5: Commit**

```bash
git add inkognito/server/
git commit -m "chore: add server package with Fastify, Prisma, config validation"
```

---

### Task 3: Prisma Schema

**Files:**
- Create: `inkognito/server/prisma/schema.prisma`

- [ ] **Step 1: Write the full Prisma schema**

All tables from the spec — users, privacy_settings, artists, sticker_packs, stickers, tips, chat_sessions, contacts, blocks, reports.

```prisma
// server/prisma/schema.prisma
generator client {
  provider = "prisma-client-js"
}

datasource db {
  provider = "postgresql"
  url      = env("DATABASE_URL")
}

enum Gender {
  male
  female
  nonbinary
  unset
}

enum InterestedIn {
  male
  female
  everyone
  random
}

enum ArtistStatus {
  invited
  active
  suspended
}

enum StickerCategory {
  flirty
  spicy
  romantic
  playful
  emoji
}

enum StickerFileType {
  webp
  png
  mp4
  webm
}

enum MatchType {
  random
  invite
  discover
}

model User {
  id            String   @id @default(uuid()) @db.Uuid
  telegramId    BigInt   @unique @map("telegram_id")
  nickname      String   @unique @db.VarChar(30)
  avatarUrl     String?  @map("avatar_url")
  gender        Gender   @default(unset)
  interestedIn  InterestedIn @default(everyone) @map("interested_in")
  ageConfirmed  Boolean  @default(false) @map("age_confirmed")
  discoverable  Boolean  @default(false)
  createdAt     DateTime @default(now()) @map("created_at")
  lastSeenAt    DateTime @default(now()) @map("last_seen_at")

  privacySettings PrivacySettings?
  artist          Artist?
  tipsSent        Tip[]           @relation("TipsSent")
  contactsOwned   Contact[]       @relation("ContactsOwned")
  contactOf       Contact[]       @relation("ContactOf")
  blocksCreated   Block[]         @relation("BlocksCreated")
  blockedBy       Block[]         @relation("BlockedBy")
  reportsCreated  Report[]        @relation("ReportsCreated")
  reportsReceived Report[]        @relation("ReportsReceived")
  sessionsAsA     ChatSession[]   @relation("SessionUserA")
  sessionsAsB     ChatSession[]   @relation("SessionUserB")

  @@map("users")
}

model PrivacySettings {
  userId               String  @id @map("user_id") @db.Uuid
  disappearingMessages Boolean @default(true) @map("disappearing_messages")
  disappearTimerSec    Int?    @map("disappear_timer_sec")
  screenshotDetection  Boolean @default(false) @map("screenshot_detection")
  hiddenChatsPin       String? @map("hidden_chats_pin") @db.VarChar(255)
  typingIndicator      Boolean @default(true) @map("typing_indicator")
  readReceipts         Boolean @default(true) @map("read_receipts")
  showOnlineStatus     Boolean @default(true) @map("show_online_status")

  user User @relation(fields: [userId], references: [id], onDelete: Cascade)

  @@map("privacy_settings")
}

model Artist {
  id             String       @id @default(uuid()) @db.Uuid
  userId         String       @unique @map("user_id") @db.Uuid
  displayName    String       @map("display_name") @db.VarChar(40)
  bio            String       @default("")
  status         ArtistStatus @default(invited)
  totalTipsStars BigInt       @default(0) @map("total_tips_stars")
  approvedAt     DateTime?    @map("approved_at")

  user         User          @relation(fields: [userId], references: [id], onDelete: Cascade)
  stickerPacks StickerPack[]
  tipsReceived Tip[]         @relation("TipsReceived")

  @@map("artists")
}

model StickerPack {
  id           String          @id @default(uuid()) @db.Uuid
  artistId     String          @map("artist_id") @db.Uuid
  name         String          @db.VarChar(60)
  category     StickerCategory
  thumbnailUrl String?         @map("thumbnail_url")
  stickerCount Int             @default(0) @map("sticker_count")
  useCount     BigInt          @default(0) @map("use_count")
  tipCount     BigInt          @default(0) @map("tip_count")
  isFeatured   Boolean         @default(false) @map("is_featured")
  createdAt    DateTime        @default(now()) @map("created_at")

  artist   Artist    @relation(fields: [artistId], references: [id], onDelete: Cascade)
  stickers Sticker[]
  tips     Tip[]

  @@map("sticker_packs")
}

model Sticker {
  id        String          @id @default(uuid()) @db.Uuid
  packId    String          @map("pack_id") @db.Uuid
  fileUrl   String          @map("file_url")
  fileType  StickerFileType @map("file_type")
  isAnimated Boolean        @default(false) @map("is_animated")
  sortOrder  Int            @default(0) @map("sort_order")

  pack StickerPack @relation(fields: [packId], references: [id], onDelete: Cascade)

  @@map("stickers")
}

model Tip {
  id                String   @id @default(uuid()) @db.Uuid
  fromUserId        String   @map("from_user_id") @db.Uuid
  toArtistId        String   @map("to_artist_id") @db.Uuid
  stickerPackId     String?  @map("sticker_pack_id") @db.Uuid
  amountStars       Int      @map("amount_stars")
  telegramPaymentId String   @map("telegram_payment_id")
  createdAt         DateTime @default(now()) @map("created_at")

  fromUser    User         @relation("TipsSent", fields: [fromUserId], references: [id])
  toArtist    Artist       @relation("TipsReceived", fields: [toArtistId], references: [id])
  stickerPack StickerPack? @relation(fields: [stickerPackId], references: [id])

  @@map("tips")
}

model ChatSession {
  id        String    @id @default(uuid()) @db.Uuid
  userAId   String    @map("user_a_id") @db.Uuid
  userBId   String    @map("user_b_id") @db.Uuid
  category  String    @db.VarChar(30)
  matchType MatchType @map("match_type")
  startedAt DateTime  @default(now()) @map("started_at")
  endedAt   DateTime? @map("ended_at")

  userA   User     @relation("SessionUserA", fields: [userAId], references: [id])
  userB   User     @relation("SessionUserB", fields: [userBId], references: [id])
  reports Report[]

  @@map("chat_sessions")
}

model Contact {
  id              String  @id @default(uuid()) @db.Uuid
  ownerId         String  @map("owner_id") @db.Uuid
  contactUserId   String  @map("contact_user_id") @db.Uuid
  contactNickname String  @map("contact_nickname") @db.VarChar(30)
  isHidden        Boolean @default(false) @map("is_hidden")
  isFavorite      Boolean @default(false) @map("is_favorite")
  createdAt       DateTime @default(now()) @map("created_at")

  owner       User @relation("ContactsOwned", fields: [ownerId], references: [id], onDelete: Cascade)
  contactUser User @relation("ContactOf", fields: [contactUserId], references: [id], onDelete: Cascade)

  @@unique([ownerId, contactUserId])
  @@map("contacts")
}

model Block {
  blockerId String   @map("blocker_id") @db.Uuid
  blockedId String   @map("blocked_id") @db.Uuid
  createdAt DateTime @default(now()) @map("created_at")

  blocker User @relation("BlocksCreated", fields: [blockerId], references: [id], onDelete: Cascade)
  blocked User @relation("BlockedBy", fields: [blockedId], references: [id], onDelete: Cascade)

  @@id([blockerId, blockedId])
  @@map("blocks")
}

model Report {
  id             String   @id @default(uuid()) @db.Uuid
  reporterId     String   @map("reporter_id") @db.Uuid
  reportedUserId String   @map("reported_user_id") @db.Uuid
  chatSessionId  String?  @map("chat_session_id") @db.Uuid
  reason         String
  createdAt      DateTime @default(now()) @map("created_at")

  reporter     User         @relation("ReportsCreated", fields: [reporterId], references: [id])
  reportedUser User         @relation("ReportsReceived", fields: [reportedUserId], references: [id])
  chatSession  ChatSession? @relation(fields: [chatSessionId], references: [id])

  @@map("reports")
}
```

- [ ] **Step 2: Create .env for development**

```bash
cd /Users/nemasha/MyProjects/gdpr-agent/sexting/inkognito
cp .env.example .env
```

Edit `.env` with real values:
```
DATABASE_URL=postgresql://inkognito:inkognito_dev@localhost:5432/inkognito
REDIS_URL=redis://localhost:6379
BOT_TOKEN=placeholder_get_from_botfather
JWT_SECRET=dev_secret_minimum_16_chars
TELEGRAM_ID_ENCRYPTION_KEY=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
```

- [ ] **Step 3: Generate Prisma client and run migration**

Run: `cd /Users/nemasha/MyProjects/gdpr-agent/sexting/inkognito/server && npx prisma migrate dev --name init`
Expected: Migration created and applied. Output includes "Your database is now in sync with your schema."

Run: `npx prisma generate`
Expected: "✔ Generated Prisma Client"

- [ ] **Step 4: Verify schema by listing tables**

Run: `docker compose exec postgres psql -U inkognito -c "\dt"`
Expected: Tables listed — users, privacy_settings, artists, sticker_packs, stickers, tips, chat_sessions, contacts, blocks, reports.

- [ ] **Step 5: Commit**

```bash
git add inkognito/server/prisma/ inkognito/.env.example
git commit -m "feat: add Prisma schema with all tables from spec"
```

---

### Task 4: Prisma & Redis Client Singletons

**Files:**
- Create: `inkognito/server/src/lib/prisma.ts`
- Create: `inkognito/server/src/lib/redis.ts`

- [ ] **Step 1: Write Prisma singleton**

```typescript
// server/src/lib/prisma.ts
import { PrismaClient } from "@prisma/client";

let prisma: PrismaClient;

export function getPrisma(): PrismaClient {
  if (!prisma) {
    prisma = new PrismaClient({
      log: process.env.NODE_ENV === "development" ? ["warn", "error"] : ["error"],
    });
  }
  return prisma;
}

export async function disconnectPrisma(): Promise<void> {
  if (prisma) {
    await prisma.$disconnect();
  }
}
```

- [ ] **Step 2: Write Redis singleton**

```typescript
// server/src/lib/redis.ts
import Redis from "ioredis";

let redis: Redis;

export function getRedis(): Redis {
  if (!redis) {
    redis = new Redis(process.env.REDIS_URL ?? "redis://localhost:6379", {
      maxRetriesPerRequest: 3,
      lazyConnect: true,
    });
  }
  return redis;
}

export async function disconnectRedis(): Promise<void> {
  if (redis) {
    await redis.quit();
  }
}
```

- [ ] **Step 3: Verify imports compile**

Run: `cd /Users/nemasha/MyProjects/gdpr-agent/sexting/inkognito/server && npx tsc --noEmit`
Expected: No errors.

- [ ] **Step 4: Commit**

```bash
git add inkognito/server/src/lib/
git commit -m "feat: add Prisma and Redis client singletons"
```

---

### Task 5: Telegram ID Encryption

**Files:**
- Create: `inkognito/server/src/utils/crypto.ts`
- Create: `inkognito/server/tests/utils/crypto.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// server/tests/utils/crypto.test.ts
import { describe, it, expect } from "vitest";
import { encryptTelegramId, decryptTelegramId } from "../../src/utils/crypto.js";

describe("Telegram ID encryption", () => {
  const testKey = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

  it("round-trips a telegram ID", () => {
    const telegramId = 123456789n;
    const encrypted = encryptTelegramId(telegramId, testKey);
    const decrypted = decryptTelegramId(encrypted, testKey);
    expect(decrypted).toBe(telegramId);
  });

  it("produces different ciphertext for different IDs", () => {
    const a = encryptTelegramId(111n, testKey);
    const b = encryptTelegramId(222n, testKey);
    expect(a).not.toBe(b);
  });

  it("produces different ciphertext each call (random IV)", () => {
    const a = encryptTelegramId(123n, testKey);
    const b = encryptTelegramId(123n, testKey);
    expect(a).not.toBe(b);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/nemasha/MyProjects/gdpr-agent/sexting/inkognito/server && npx vitest run tests/utils/crypto.test.ts`
Expected: FAIL — cannot find module `../../src/utils/crypto.js`

- [ ] **Step 3: Write implementation**

```typescript
// server/src/utils/crypto.ts
import { createCipheriv, createDecipheriv, randomBytes } from "node:crypto";

const ALGORITHM = "aes-256-gcm";
const IV_LENGTH = 12;
const TAG_LENGTH = 16;

export function encryptTelegramId(telegramId: bigint, hexKey: string): string {
  const key = Buffer.from(hexKey, "hex");
  const iv = randomBytes(IV_LENGTH);
  const cipher = createCipheriv(ALGORITHM, key, iv);

  const plaintext = Buffer.alloc(8);
  plaintext.writeBigInt64BE(telegramId);

  const encrypted = Buffer.concat([cipher.update(plaintext), cipher.final()]);
  const tag = cipher.getAuthTag();

  return Buffer.concat([iv, tag, encrypted]).toString("base64url");
}

export function decryptTelegramId(ciphertext: string, hexKey: string): bigint {
  const key = Buffer.from(hexKey, "hex");
  const data = Buffer.from(ciphertext, "base64url");

  const iv = data.subarray(0, IV_LENGTH);
  const tag = data.subarray(IV_LENGTH, IV_LENGTH + TAG_LENGTH);
  const encrypted = data.subarray(IV_LENGTH + TAG_LENGTH);

  const decipher = createDecipheriv(ALGORITHM, key, iv);
  decipher.setAuthTag(tag);

  const decrypted = Buffer.concat([decipher.update(encrypted), decipher.final()]);
  return decrypted.readBigInt64BE();
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/nemasha/MyProjects/gdpr-agent/sexting/inkognito/server && npx vitest run tests/utils/crypto.test.ts`
Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add inkognito/server/src/utils/crypto.ts inkognito/server/tests/utils/crypto.test.ts
git commit -m "feat: add AES-256-GCM encryption for Telegram IDs"
```

---

### Task 6: Telegram initData Validation

**Files:**
- Create: `inkognito/server/src/lib/telegram.ts`
- Create: `inkognito/server/tests/lib/telegram.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// server/tests/lib/telegram.test.ts
import { describe, it, expect } from "vitest";
import { createHmac } from "node:crypto";
import { validateInitData, type TelegramUser } from "../../src/lib/telegram.js";

function createFakeInitData(botToken: string, user: TelegramUser, authDate: number): string {
  const params = new URLSearchParams();
  params.set("user", JSON.stringify(user));
  params.set("auth_date", String(authDate));

  // Telegram's validation: HMAC-SHA256 of sorted key=value pairs
  const secretKey = createHmac("sha256", "WebAppData").update(botToken).digest();
  const checkPairs = Array.from(params.entries())
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([k, v]) => `${k}=${v}`)
    .join("\n");
  const hash = createHmac("sha256", secretKey).update(checkPairs).digest("hex");
  params.set("hash", hash);

  return params.toString();
}

const TEST_BOT_TOKEN = "123456:ABC-DEF";
const TEST_USER: TelegramUser = { id: 42, first_name: "Test", last_name: "User", username: "testuser" };

describe("validateInitData", () => {
  it("returns user for valid initData", () => {
    const now = Math.floor(Date.now() / 1000);
    const initData = createFakeInitData(TEST_BOT_TOKEN, TEST_USER, now);
    const result = validateInitData(initData, TEST_BOT_TOKEN);
    expect(result.id).toBe(42);
    expect(result.first_name).toBe("Test");
  });

  it("throws on tampered hash", () => {
    const now = Math.floor(Date.now() / 1000);
    const initData = createFakeInitData(TEST_BOT_TOKEN, TEST_USER, now);
    const tampered = initData.replace(/hash=[^&]+/, "hash=deadbeef");
    expect(() => validateInitData(tampered, TEST_BOT_TOKEN)).toThrow("Invalid initData signature");
  });

  it("throws on expired auth_date (older than 5 minutes)", () => {
    const old = Math.floor(Date.now() / 1000) - 600;
    const initData = createFakeInitData(TEST_BOT_TOKEN, TEST_USER, old);
    expect(() => validateInitData(initData, TEST_BOT_TOKEN)).toThrow("initData expired");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/nemasha/MyProjects/gdpr-agent/sexting/inkognito/server && npx vitest run tests/lib/telegram.test.ts`
Expected: FAIL — cannot find module `../../src/lib/telegram.js`

- [ ] **Step 3: Write implementation**

```typescript
// server/src/lib/telegram.ts
import { createHmac } from "node:crypto";

export interface TelegramUser {
  id: number;
  first_name: string;
  last_name?: string;
  username?: string;
}

const MAX_AGE_SECONDS = 300; // 5 minutes

export function validateInitData(initDataRaw: string, botToken: string): TelegramUser {
  const params = new URLSearchParams(initDataRaw);
  const hash = params.get("hash");
  if (!hash) throw new Error("Missing hash in initData");

  const authDate = Number(params.get("auth_date"));
  if (!authDate) throw new Error("Missing auth_date in initData");

  const now = Math.floor(Date.now() / 1000);
  if (now - authDate > MAX_AGE_SECONDS) {
    throw new Error("initData expired");
  }

  // Build check string: sorted key=value pairs excluding hash
  params.delete("hash");
  const checkPairs = Array.from(params.entries())
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([k, v]) => `${k}=${v}`)
    .join("\n");

  const secretKey = createHmac("sha256", "WebAppData").update(botToken).digest();
  const computedHash = createHmac("sha256", secretKey).update(checkPairs).digest("hex");

  if (computedHash !== hash) {
    throw new Error("Invalid initData signature");
  }

  const userStr = params.get("user");
  if (!userStr) throw new Error("Missing user in initData");

  return JSON.parse(userStr) as TelegramUser;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/nemasha/MyProjects/gdpr-agent/sexting/inkognito/server && npx vitest run tests/lib/telegram.test.ts`
Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add inkognito/server/src/lib/telegram.ts inkognito/server/tests/lib/telegram.test.ts
git commit -m "feat: add Telegram initData HMAC validation"
```

---

### Task 7: JWT Auth Middleware

**Files:**
- Create: `inkognito/server/src/middleware/auth.ts`

- [ ] **Step 1: Write JWT helper functions**

```typescript
// server/src/middleware/auth.ts
import { SignJWT, jwtVerify } from "jose";
import type { FastifyRequest, FastifyReply } from "fastify";

export interface JwtPayload {
  sub: string; // user UUID
  iat: number;
}

let jwtSecret: Uint8Array;

function getSecret(): Uint8Array {
  if (!jwtSecret) {
    jwtSecret = new TextEncoder().encode(process.env.JWT_SECRET);
  }
  return jwtSecret;
}

export async function signJwt(userId: string): Promise<string> {
  return new SignJWT({ sub: userId })
    .setProtectedHeader({ alg: "HS256" })
    .setIssuedAt()
    .setExpirationTime("7d")
    .sign(getSecret());
}

export async function verifyJwt(token: string): Promise<JwtPayload> {
  const { payload } = await jwtVerify(token, getSecret());
  return payload as unknown as JwtPayload;
}

export async function authGuard(request: FastifyRequest, reply: FastifyReply): Promise<void> {
  const header = request.headers.authorization;
  if (!header?.startsWith("Bearer ")) {
    reply.code(401).send({ error: "Missing authorization header" });
    return;
  }
  const token = header.slice(7);
  try {
    const payload = await verifyJwt(token);
    (request as any).userId = payload.sub;
  } catch {
    reply.code(401).send({ error: "Invalid or expired token" });
  }
}
```

- [ ] **Step 2: Verify it compiles**

Run: `cd /Users/nemasha/MyProjects/gdpr-agent/sexting/inkognito/server && npx tsc --noEmit`
Expected: No errors.

- [ ] **Step 3: Commit**

```bash
git add inkognito/server/src/middleware/auth.ts
git commit -m "feat: add JWT sign/verify and Fastify auth guard middleware"
```

---

### Task 8: Auth Route

**Files:**
- Create: `inkognito/server/src/routes/auth.ts`
- Create: `inkognito/server/tests/routes/auth.test.ts`
- Create: `inkognito/server/tests/helpers/setup.ts`

- [ ] **Step 1: Write test helpers**

```typescript
// server/tests/helpers/setup.ts
import Fastify, { type FastifyInstance } from "fastify";
import { getPrisma } from "../../src/lib/prisma.js";
import { createHmac } from "node:crypto";
import type { TelegramUser } from "../../src/lib/telegram.js";

export const TEST_BOT_TOKEN = "123456:ABC-DEF";

export async function buildApp(registerRoutes: (app: FastifyInstance) => void): Promise<FastifyInstance> {
  process.env.BOT_TOKEN = TEST_BOT_TOKEN;
  process.env.JWT_SECRET = "test_secret_minimum_16";
  process.env.TELEGRAM_ID_ENCRYPTION_KEY = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

  const app = Fastify({ logger: false });
  registerRoutes(app);
  await app.ready();
  return app;
}

export function createInitData(user: TelegramUser, botToken: string = TEST_BOT_TOKEN): string {
  const params = new URLSearchParams();
  params.set("user", JSON.stringify(user));
  params.set("auth_date", String(Math.floor(Date.now() / 1000)));

  const secretKey = createHmac("sha256", "WebAppData").update(botToken).digest();
  const checkPairs = Array.from(params.entries())
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([k, v]) => `${k}=${v}`)
    .join("\n");
  const hash = createHmac("sha256", secretKey).update(checkPairs).digest("hex");
  params.set("hash", hash);
  return params.toString();
}

export async function cleanupDb(): Promise<void> {
  const prisma = getPrisma();
  await prisma.report.deleteMany();
  await prisma.block.deleteMany();
  await prisma.contact.deleteMany();
  await prisma.tip.deleteMany();
  await prisma.sticker.deleteMany();
  await prisma.stickerPack.deleteMany();
  await prisma.artist.deleteMany();
  await prisma.chatSession.deleteMany();
  await prisma.privacySettings.deleteMany();
  await prisma.user.deleteMany();
}
```

- [ ] **Step 2: Write failing auth route test**

```typescript
// server/tests/routes/auth.test.ts
import { describe, it, expect, beforeEach, afterAll } from "vitest";
import { buildApp, createInitData, cleanupDb } from "../helpers/setup.js";
import { registerAuthRoutes } from "../../src/routes/auth.js";
import { getPrisma, disconnectPrisma } from "../../src/lib/prisma.js";

describe("POST /auth/telegram", () => {
  beforeEach(async () => {
    await cleanupDb();
  });

  afterAll(async () => {
    await cleanupDb();
    await disconnectPrisma();
  });

  it("creates user and returns JWT on first login", async () => {
    const app = await buildApp((a) => registerAuthRoutes(a));
    const initData = createInitData({ id: 42, first_name: "Alice" });

    const res = await app.inject({
      method: "POST",
      url: "/auth/telegram",
      payload: { initData },
    });

    expect(res.statusCode).toBe(200);
    const body = res.json();
    expect(body.token).toBeDefined();
    expect(body.user.nickname).toMatch(/^user_/);
    expect(body.isNewUser).toBe(true);

    // Verify user exists in DB
    const prisma = getPrisma();
    const user = await prisma.user.findFirst({ where: { telegramId: 42n } });
    expect(user).not.toBeNull();

    await app.close();
  });

  it("returns existing user on second login", async () => {
    const app = await buildApp((a) => registerAuthRoutes(a));
    const initData = createInitData({ id: 42, first_name: "Alice" });

    await app.inject({ method: "POST", url: "/auth/telegram", payload: { initData } });
    const res = await app.inject({ method: "POST", url: "/auth/telegram", payload: { initData } });

    expect(res.statusCode).toBe(200);
    expect(res.json().isNewUser).toBe(false);

    await app.close();
  });

  it("rejects invalid initData", async () => {
    const app = await buildApp((a) => registerAuthRoutes(a));

    const res = await app.inject({
      method: "POST",
      url: "/auth/telegram",
      payload: { initData: "user=bad&hash=fake&auth_date=0" },
    });

    expect(res.statusCode).toBe(401);

    await app.close();
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /Users/nemasha/MyProjects/gdpr-agent/sexting/inkognito/server && npx vitest run tests/routes/auth.test.ts`
Expected: FAIL — cannot find module `../../src/routes/auth.js`

- [ ] **Step 4: Write auth route implementation**

```typescript
// server/src/routes/auth.ts
import type { FastifyInstance } from "fastify";
import { z } from "zod";
import { validateInitData } from "../lib/telegram.js";
import { getPrisma } from "../lib/prisma.js";
import { signJwt } from "../middleware/auth.js";

const authBodySchema = z.object({
  initData: z.string().min(1),
});

export function registerAuthRoutes(app: FastifyInstance): void {
  app.post("/auth/telegram", async (request, reply) => {
    const body = authBodySchema.safeParse(request.body);
    if (!body.success) {
      return reply.code(400).send({ error: "initData is required" });
    }

    let telegramUser;
    try {
      telegramUser = validateInitData(body.data.initData, process.env.BOT_TOKEN!);
    } catch (err: any) {
      return reply.code(401).send({ error: err.message });
    }

    const prisma = getPrisma();
    const telegramId = BigInt(telegramUser.id);

    // Find or create user
    let user = await prisma.user.findUnique({ where: { telegramId } });
    let isNewUser = false;

    if (!user) {
      // Generate a temporary nickname — user will customize in profile creation
      const tempNickname = `user_${Date.now().toString(36)}`;
      user = await prisma.user.create({
        data: {
          telegramId,
          nickname: tempNickname,
          privacySettings: { create: {} },
        },
      });
      isNewUser = true;
    }

    // Update last seen
    await prisma.user.update({
      where: { id: user.id },
      data: { lastSeenAt: new Date() },
    });

    const token = await signJwt(user.id);

    return {
      token,
      user: {
        id: user.id,
        nickname: user.nickname,
        ageConfirmed: user.ageConfirmed,
        gender: user.gender,
        interestedIn: user.interestedIn,
      },
      isNewUser,
    };
  });
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /Users/nemasha/MyProjects/gdpr-agent/sexting/inkognito/server && npx vitest run tests/routes/auth.test.ts`
Expected: 3 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add inkognito/server/src/routes/auth.ts inkognito/server/tests/routes/auth.test.ts inkognito/server/tests/helpers/setup.ts
git commit -m "feat: add Telegram auth route — validates initData, creates user, returns JWT"
```

---

### Task 9: User Profile Routes

**Files:**
- Create: `inkognito/server/src/routes/users.ts`
- Create: `inkognito/server/tests/routes/users.test.ts`

- [ ] **Step 1: Write failing tests**

```typescript
// server/tests/routes/users.test.ts
import { describe, it, expect, beforeEach, afterAll } from "vitest";
import { buildApp, createInitData, cleanupDb } from "../helpers/setup.js";
import { registerAuthRoutes } from "../../src/routes/auth.js";
import { registerUserRoutes } from "../../src/routes/users.js";
import { getPrisma, disconnectPrisma } from "../../src/lib/prisma.js";
import type { FastifyInstance } from "fastify";

async function createAuthenticatedUser(app: FastifyInstance, telegramId = 42): Promise<string> {
  const initData = createInitData({ id: telegramId, first_name: "Test" });
  const res = await app.inject({ method: "POST", url: "/auth/telegram", payload: { initData } });
  return res.json().token;
}

describe("User routes", () => {
  beforeEach(async () => {
    await cleanupDb();
  });

  afterAll(async () => {
    await cleanupDb();
    await disconnectPrisma();
  });

  describe("GET /users/me", () => {
    it("returns current user profile", async () => {
      const app = await buildApp((a) => { registerAuthRoutes(a); registerUserRoutes(a); });
      const token = await createAuthenticatedUser(app);

      const res = await app.inject({
        method: "GET",
        url: "/users/me",
        headers: { authorization: `Bearer ${token}` },
      });

      expect(res.statusCode).toBe(200);
      const body = res.json();
      expect(body.nickname).toMatch(/^user_/);
      expect(body.ageConfirmed).toBe(false);

      await app.close();
    });

    it("returns 401 without token", async () => {
      const app = await buildApp((a) => { registerUserRoutes(a); });
      const res = await app.inject({ method: "GET", url: "/users/me" });
      expect(res.statusCode).toBe(401);
      await app.close();
    });
  });

  describe("PUT /users/me", () => {
    it("updates nickname and preferences", async () => {
      const app = await buildApp((a) => { registerAuthRoutes(a); registerUserRoutes(a); });
      const token = await createAuthenticatedUser(app);

      const res = await app.inject({
        method: "PUT",
        url: "/users/me",
        headers: { authorization: `Bearer ${token}` },
        payload: { nickname: "hotshot", gender: "male", interestedIn: "female" },
      });

      expect(res.statusCode).toBe(200);
      expect(res.json().nickname).toBe("hotshot");
      expect(res.json().gender).toBe("male");

      await app.close();
    });

    it("rejects duplicate nickname", async () => {
      const app = await buildApp((a) => { registerAuthRoutes(a); registerUserRoutes(a); });
      const token1 = await createAuthenticatedUser(app, 42);
      const token2 = await createAuthenticatedUser(app, 99);

      await app.inject({
        method: "PUT",
        url: "/users/me",
        headers: { authorization: `Bearer ${token1}` },
        payload: { nickname: "taken" },
      });

      const res = await app.inject({
        method: "PUT",
        url: "/users/me",
        headers: { authorization: `Bearer ${token2}` },
        payload: { nickname: "taken" },
      });

      expect(res.statusCode).toBe(409);

      await app.close();
    });

    it("rejects nickname shorter than 3 chars", async () => {
      const app = await buildApp((a) => { registerAuthRoutes(a); registerUserRoutes(a); });
      const token = await createAuthenticatedUser(app);

      const res = await app.inject({
        method: "PUT",
        url: "/users/me",
        headers: { authorization: `Bearer ${token}` },
        payload: { nickname: "ab" },
      });

      expect(res.statusCode).toBe(400);

      await app.close();
    });
  });

  describe("POST /users/me/age-confirm", () => {
    it("sets ageConfirmed to true", async () => {
      const app = await buildApp((a) => { registerAuthRoutes(a); registerUserRoutes(a); });
      const token = await createAuthenticatedUser(app);

      const res = await app.inject({
        method: "POST",
        url: "/users/me/age-confirm",
        headers: { authorization: `Bearer ${token}` },
      });

      expect(res.statusCode).toBe(200);
      expect(res.json().ageConfirmed).toBe(true);

      await app.close();
    });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/nemasha/MyProjects/gdpr-agent/sexting/inkognito/server && npx vitest run tests/routes/users.test.ts`
Expected: FAIL — cannot find module `../../src/routes/users.js`

- [ ] **Step 3: Write user routes implementation**

```typescript
// server/src/routes/users.ts
import type { FastifyInstance } from "fastify";
import { z } from "zod";
import { getPrisma } from "../lib/prisma.js";
import { authGuard } from "../middleware/auth.js";

const updateProfileSchema = z.object({
  nickname: z.string().min(3).max(30).regex(/^[a-zA-Z0-9_]+$/, "Alphanumeric and underscores only").optional(),
  avatarUrl: z.string().url().nullable().optional(),
  gender: z.enum(["male", "female", "nonbinary", "unset"]).optional(),
  interestedIn: z.enum(["male", "female", "everyone", "random"]).optional(),
  discoverable: z.boolean().optional(),
});

export function registerUserRoutes(app: FastifyInstance): void {
  app.get("/users/me", { preHandler: authGuard }, async (request, reply) => {
    const userId = (request as any).userId;
    const prisma = getPrisma();

    const user = await prisma.user.findUnique({ where: { id: userId } });
    if (!user) return reply.code(404).send({ error: "User not found" });

    return {
      id: user.id,
      nickname: user.nickname,
      avatarUrl: user.avatarUrl,
      gender: user.gender,
      interestedIn: user.interestedIn,
      ageConfirmed: user.ageConfirmed,
      discoverable: user.discoverable,
      createdAt: user.createdAt.toISOString(),
    };
  });

  app.put("/users/me", { preHandler: authGuard }, async (request, reply) => {
    const userId = (request as any).userId;
    const parsed = updateProfileSchema.safeParse(request.body);
    if (!parsed.success) {
      return reply.code(400).send({ error: parsed.error.issues[0].message });
    }

    const prisma = getPrisma();

    // Check nickname uniqueness if changing
    if (parsed.data.nickname) {
      const existing = await prisma.user.findUnique({ where: { nickname: parsed.data.nickname } });
      if (existing && existing.id !== userId) {
        return reply.code(409).send({ error: "Nickname already taken" });
      }
    }

    const user = await prisma.user.update({
      where: { id: userId },
      data: parsed.data,
    });

    return {
      id: user.id,
      nickname: user.nickname,
      avatarUrl: user.avatarUrl,
      gender: user.gender,
      interestedIn: user.interestedIn,
      ageConfirmed: user.ageConfirmed,
      discoverable: user.discoverable,
    };
  });

  app.post("/users/me/age-confirm", { preHandler: authGuard }, async (request, reply) => {
    const userId = (request as any).userId;
    const prisma = getPrisma();

    const user = await prisma.user.update({
      where: { id: userId },
      data: { ageConfirmed: true },
    });

    return { ageConfirmed: user.ageConfirmed };
  });
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/nemasha/MyProjects/gdpr-agent/sexting/inkognito/server && npx vitest run tests/routes/users.test.ts`
Expected: 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add inkognito/server/src/routes/users.ts inkognito/server/tests/routes/users.test.ts
git commit -m "feat: add user profile routes — GET/PUT /users/me, POST age-confirm"
```

---

### Task 10: Privacy Settings Routes

**Files:**
- Create: `inkognito/server/src/routes/privacy.ts`
- Create: `inkognito/server/tests/routes/privacy.test.ts`

- [ ] **Step 1: Write failing tests**

```typescript
// server/tests/routes/privacy.test.ts
import { describe, it, expect, beforeEach, afterAll } from "vitest";
import { buildApp, createInitData, cleanupDb } from "../helpers/setup.js";
import { registerAuthRoutes } from "../../src/routes/auth.js";
import { registerPrivacyRoutes } from "../../src/routes/privacy.js";
import { disconnectPrisma } from "../../src/lib/prisma.js";
import type { FastifyInstance } from "fastify";

async function createAuthenticatedUser(app: FastifyInstance, telegramId = 42): Promise<string> {
  const initData = createInitData({ id: telegramId, first_name: "Test" });
  const res = await app.inject({ method: "POST", url: "/auth/telegram", payload: { initData } });
  return res.json().token;
}

describe("Privacy routes", () => {
  beforeEach(async () => {
    await cleanupDb();
  });

  afterAll(async () => {
    await cleanupDb();
    await disconnectPrisma();
  });

  describe("GET /users/me/privacy", () => {
    it("returns default privacy settings", async () => {
      const app = await buildApp((a) => { registerAuthRoutes(a); registerPrivacyRoutes(a); });
      const token = await createAuthenticatedUser(app);

      const res = await app.inject({
        method: "GET",
        url: "/users/me/privacy",
        headers: { authorization: `Bearer ${token}` },
      });

      expect(res.statusCode).toBe(200);
      const body = res.json();
      expect(body.disappearingMessages).toBe(true);
      expect(body.screenshotDetection).toBe(false);
      expect(body.typingIndicator).toBe(true);
      expect(body.readReceipts).toBe(true);
      expect(body.showOnlineStatus).toBe(true);

      await app.close();
    });
  });

  describe("PUT /users/me/privacy", () => {
    it("updates individual privacy toggles", async () => {
      const app = await buildApp((a) => { registerAuthRoutes(a); registerPrivacyRoutes(a); });
      const token = await createAuthenticatedUser(app);

      const res = await app.inject({
        method: "PUT",
        url: "/users/me/privacy",
        headers: { authorization: `Bearer ${token}` },
        payload: { screenshotDetection: true, readReceipts: false, disappearTimerSec: 30 },
      });

      expect(res.statusCode).toBe(200);
      const body = res.json();
      expect(body.screenshotDetection).toBe(true);
      expect(body.readReceipts).toBe(false);
      expect(body.disappearTimerSec).toBe(30);
      // Unchanged fields keep defaults
      expect(body.typingIndicator).toBe(true);

      await app.close();
    });

    it("rejects invalid disappear timer", async () => {
      const app = await buildApp((a) => { registerAuthRoutes(a); registerPrivacyRoutes(a); });
      const token = await createAuthenticatedUser(app);

      const res = await app.inject({
        method: "PUT",
        url: "/users/me/privacy",
        headers: { authorization: `Bearer ${token}` },
        payload: { disappearTimerSec: -5 },
      });

      expect(res.statusCode).toBe(400);

      await app.close();
    });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/nemasha/MyProjects/gdpr-agent/sexting/inkognito/server && npx vitest run tests/routes/privacy.test.ts`
Expected: FAIL — cannot find module `../../src/routes/privacy.js`

- [ ] **Step 3: Write privacy routes implementation**

```typescript
// server/src/routes/privacy.ts
import type { FastifyInstance } from "fastify";
import { z } from "zod";
import { getPrisma } from "../lib/prisma.js";
import { authGuard } from "../middleware/auth.js";

const VALID_TIMERS = [5, 30, 300]; // 5s, 30s, 5min

const updatePrivacySchema = z.object({
  disappearingMessages: z.boolean().optional(),
  disappearTimerSec: z.number().refine((v) => v === null || VALID_TIMERS.includes(v), {
    message: `Timer must be one of: ${VALID_TIMERS.join(", ")} (seconds)`,
  }).nullable().optional(),
  screenshotDetection: z.boolean().optional(),
  typingIndicator: z.boolean().optional(),
  readReceipts: z.boolean().optional(),
  showOnlineStatus: z.boolean().optional(),
});

function formatSettings(settings: any) {
  return {
    disappearingMessages: settings.disappearingMessages,
    disappearTimerSec: settings.disappearTimerSec,
    screenshotDetection: settings.screenshotDetection,
    typingIndicator: settings.typingIndicator,
    readReceipts: settings.readReceipts,
    showOnlineStatus: settings.showOnlineStatus,
  };
}

export function registerPrivacyRoutes(app: FastifyInstance): void {
  app.get("/users/me/privacy", { preHandler: authGuard }, async (request, reply) => {
    const userId = (request as any).userId;
    const prisma = getPrisma();

    const settings = await prisma.privacySettings.findUnique({ where: { userId } });
    if (!settings) return reply.code(404).send({ error: "Privacy settings not found" });

    return formatSettings(settings);
  });

  app.put("/users/me/privacy", { preHandler: authGuard }, async (request, reply) => {
    const userId = (request as any).userId;
    const parsed = updatePrivacySchema.safeParse(request.body);
    if (!parsed.success) {
      return reply.code(400).send({ error: parsed.error.issues[0].message });
    }

    const prisma = getPrisma();

    const settings = await prisma.privacySettings.update({
      where: { userId },
      data: parsed.data,
    });

    return formatSettings(settings);
  });
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/nemasha/MyProjects/gdpr-agent/sexting/inkognito/server && npx vitest run tests/routes/privacy.test.ts`
Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add inkognito/server/src/routes/privacy.ts inkognito/server/tests/routes/privacy.test.ts
git commit -m "feat: add privacy settings routes — GET/PUT /users/me/privacy"
```

---

### Task 11: Fastify Server Entry Point

**Files:**
- Create: `inkognito/server/src/index.ts`

- [ ] **Step 1: Write server entry point**

```typescript
// server/src/index.ts
import Fastify from "fastify";
import cors from "@fastify/cors";
import { registerAuthRoutes } from "./routes/auth.js";
import { registerUserRoutes } from "./routes/users.js";
import { registerPrivacyRoutes } from "./routes/privacy.js";
import { disconnectPrisma } from "./lib/prisma.js";
import { disconnectRedis } from "./lib/redis.js";

const app = Fastify({
  logger: {
    transport: process.env.NODE_ENV === "development"
      ? { target: "pino-pretty" }
      : undefined,
  },
});

await app.register(cors, { origin: true });

registerAuthRoutes(app);
registerUserRoutes(app);
registerPrivacyRoutes(app);

app.get("/health", async () => ({ status: "ok" }));

const port = Number(process.env.PORT) || 3000;

async function start() {
  try {
    await app.listen({ port, host: "0.0.0.0" });
    app.log.info(`Server listening on port ${port}`);
  } catch (err) {
    app.log.error(err);
    process.exit(1);
  }
}

async function shutdown() {
  await app.close();
  await disconnectPrisma();
  await disconnectRedis();
  process.exit(0);
}

process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);

start();
```

- [ ] **Step 2: Add pino-pretty as dev dependency**

Run: `cd /Users/nemasha/MyProjects/gdpr-agent/sexting/inkognito/server && npm install -D pino-pretty`

- [ ] **Step 3: Verify server starts**

Create a `.env` in the server directory (or ensure root `.env` is loaded):

Run: `cd /Users/nemasha/MyProjects/gdpr-agent/sexting/inkognito && cp .env.example .env` (if not done already)

Run: `cd /Users/nemasha/MyProjects/gdpr-agent/sexting/inkognito/server && timeout 5 npx tsx src/index.ts || true`
Expected: Output includes "Server listening on port 3000" before timeout kills it.

- [ ] **Step 4: Test health endpoint**

In one terminal: `cd /Users/nemasha/MyProjects/gdpr-agent/sexting/inkognito/server && npx tsx src/index.ts &`

Run: `curl http://localhost:3000/health`
Expected: `{"status":"ok"}`

Kill server: `kill %1`

- [ ] **Step 5: Commit**

```bash
git add inkognito/server/src/index.ts inkognito/server/package.json
git commit -m "feat: add Fastify server entry point with health check"
```

---

### Task 12: Frontend Scaffolding

**Files:**
- Create: `inkognito/web/package.json`
- Create: `inkognito/web/tsconfig.json`
- Create: `inkognito/web/vite.config.ts`
- Create: `inkognito/web/index.html`
- Create: `inkognito/web/src/main.tsx`
- Create: `inkognito/web/tailwind.config.js`
- Create: `inkognito/web/postcss.config.js`
- Create: `inkognito/web/src/index.css`

- [ ] **Step 1: Create web package**

```json
// web/package.json
{
  "name": "@inkognito/web",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc && vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "@tanstack/react-query": "^5.62.0",
    "@telegram-apps/sdk-react": "^2.0.0",
    "react": "^19.0.0",
    "react-dom": "^19.0.0",
    "react-router-dom": "^7.1.0",
    "zustand": "^5.0.0"
  },
  "devDependencies": {
    "@tailwindcss/vite": "^4.0.0",
    "@types/react": "^19.0.0",
    "@types/react-dom": "^19.0.0",
    "@vitejs/plugin-react": "^4.3.0",
    "tailwindcss": "^4.0.0",
    "typescript": "^5.7.0",
    "vite": "^6.0.0"
  }
}
```

- [ ] **Step 2: Create Vite and Tailwind config**

```typescript
// web/vite.config.ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      "/api": { target: "http://localhost:3000", rewrite: (p) => p.replace(/^\/api/, "") },
    },
  },
});
```

```json
// web/tsconfig.json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "bundler",
    "jsx": "react-jsx",
    "strict": true,
    "skipLibCheck": true,
    "forceConsistentCasingInFileNames": true,
    "noEmit": true
  },
  "include": ["src"]
}
```

```css
/* web/src/index.css */
@import "tailwindcss";
```

- [ ] **Step 3: Create HTML entry and React root**

```html
<!-- web/index.html -->
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Inkognito</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

```tsx
// web/src/main.tsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App } from "./App.js";
import "./index.css";

const queryClient = new QueryClient();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>
);
```

- [ ] **Step 4: Install dependencies**

Run: `cd /Users/nemasha/MyProjects/gdpr-agent/sexting/inkognito/web && npm install`
Expected: Installs without errors.

- [ ] **Step 5: Commit**

```bash
git add inkognito/web/
git commit -m "chore: scaffold React frontend with Vite, Tailwind, TanStack Query"
```

---

### Task 13: Auth Store & API Client

**Files:**
- Create: `inkognito/web/src/api/client.ts`
- Create: `inkognito/web/src/stores/auth.ts`
- Create: `inkognito/web/src/hooks/useAuth.ts`

- [ ] **Step 1: Write API client**

```typescript
// web/src/api/client.ts
const BASE_URL = "/api";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function apiFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = localStorage.getItem("inkognito_token");
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...((options.headers as Record<string, string>) ?? {}),
  };
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(`${BASE_URL}${path}`, { ...options, headers });
  const body = await res.json();
  if (!res.ok) throw new ApiError(res.status, body.error ?? "Request failed");
  return body as T;
}
```

- [ ] **Step 2: Write auth store (Zustand)**

```typescript
// web/src/stores/auth.ts
import { create } from "zustand";

interface User {
  id: string;
  nickname: string;
  ageConfirmed: boolean;
  gender: string;
  interestedIn: string;
}

interface AuthState {
  token: string | null;
  user: User | null;
  isNewUser: boolean;
  setAuth: (token: string, user: User, isNewUser: boolean) => void;
  updateUser: (user: Partial<User>) => void;
  logout: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  token: localStorage.getItem("inkognito_token"),
  user: null,
  isNewUser: false,
  setAuth: (token, user, isNewUser) => {
    localStorage.setItem("inkognito_token", token);
    set({ token, user, isNewUser });
  },
  updateUser: (partial) =>
    set((state) => ({ user: state.user ? { ...state.user, ...partial } : null })),
  logout: () => {
    localStorage.removeItem("inkognito_token");
    set({ token: null, user: null, isNewUser: false });
  },
}));
```

- [ ] **Step 3: Write useAuth hook**

```typescript
// web/src/hooks/useAuth.ts
import { useCallback } from "react";
import { apiFetch } from "../api/client.js";
import { useAuthStore } from "../stores/auth.js";

export function useAuth() {
  const { setAuth, token, user } = useAuthStore();

  const login = useCallback(async () => {
    const tg = window.Telegram?.WebApp;
    if (!tg?.initData) throw new Error("Not running inside Telegram");

    const result = await apiFetch<{ token: string; user: any; isNewUser: boolean }>(
      "/auth/telegram",
      { method: "POST", body: JSON.stringify({ initData: tg.initData }) }
    );

    setAuth(result.token, result.user, result.isNewUser);
    return result;
  }, [setAuth]);

  return { login, token, user, isAuthenticated: !!token };
}
```

- [ ] **Step 4: Verify it compiles**

Run: `cd /Users/nemasha/MyProjects/gdpr-agent/sexting/inkognito/web && npx tsc --noEmit`
Expected: No errors (may warn about missing `App.tsx` — that's the next task).

- [ ] **Step 5: Commit**

```bash
git add inkognito/web/src/api/ inkognito/web/src/stores/ inkognito/web/src/hooks/
git commit -m "feat: add API client, Zustand auth store, and useAuth hook"
```

---

### Task 14: App Shell, Pages & Routing

**Files:**
- Create: `inkognito/web/src/App.tsx`
- Create: `inkognito/web/src/pages/AgeGate.tsx`
- Create: `inkognito/web/src/pages/CreateProfile.tsx`
- Create: `inkognito/web/src/pages/Home.tsx`
- Create: `inkognito/web/src/pages/Settings.tsx`
- Create: `inkognito/web/src/components/Layout.tsx`
- Create: `inkognito/web/src/hooks/useProfile.ts`

- [ ] **Step 1: Write useProfile hook**

```tsx
// web/src/hooks/useProfile.ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../api/client.js";
import { useAuthStore } from "../stores/auth.js";

interface Profile {
  id: string;
  nickname: string;
  avatarUrl: string | null;
  gender: string;
  interestedIn: string;
  ageConfirmed: boolean;
  discoverable: boolean;
}

interface PrivacySettings {
  disappearingMessages: boolean;
  disappearTimerSec: number | null;
  screenshotDetection: boolean;
  typingIndicator: boolean;
  readReceipts: boolean;
  showOnlineStatus: boolean;
}

export function useProfile() {
  const token = useAuthStore((s) => s.token);
  const queryClient = useQueryClient();

  const profile = useQuery({
    queryKey: ["profile"],
    queryFn: () => apiFetch<Profile>("/users/me"),
    enabled: !!token,
  });

  const updateProfile = useMutation({
    mutationFn: (data: Partial<Profile>) =>
      apiFetch<Profile>("/users/me", { method: "PUT", body: JSON.stringify(data) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["profile"] }),
  });

  const confirmAge = useMutation({
    mutationFn: () =>
      apiFetch<{ ageConfirmed: boolean }>("/users/me/age-confirm", { method: "POST" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["profile"] }),
  });

  return { profile, updateProfile, confirmAge };
}

export function usePrivacy() {
  const token = useAuthStore((s) => s.token);
  const queryClient = useQueryClient();

  const privacy = useQuery({
    queryKey: ["privacy"],
    queryFn: () => apiFetch<PrivacySettings>("/users/me/privacy"),
    enabled: !!token,
  });

  const updatePrivacy = useMutation({
    mutationFn: (data: Partial<PrivacySettings>) =>
      apiFetch<PrivacySettings>("/users/me/privacy", { method: "PUT", body: JSON.stringify(data) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["privacy"] }),
  });

  return { privacy, updatePrivacy };
}
```

- [ ] **Step 2: Write Layout component**

```tsx
// web/src/components/Layout.tsx
import { type ReactNode } from "react";
import { useNavigate, useLocation } from "react-router-dom";

export function Layout({ children }: { children: ReactNode }) {
  const navigate = useNavigate();
  const location = useLocation();

  const tabs = [
    { path: "/", label: "Home", icon: "🏠" },
    { path: "/settings", label: "Settings", icon: "⚙️" },
  ];

  return (
    <div className="flex flex-col h-screen bg-black text-white">
      <main className="flex-1 overflow-y-auto p-4">{children}</main>
      <nav className="flex border-t border-white/10">
        {tabs.map((tab) => (
          <button
            key={tab.path}
            onClick={() => navigate(tab.path)}
            className={`flex-1 py-3 text-center text-sm ${
              location.pathname === tab.path ? "text-violet-400" : "text-white/50"
            }`}
          >
            <div className="text-lg">{tab.icon}</div>
            {tab.label}
          </button>
        ))}
      </nav>
    </div>
  );
}
```

- [ ] **Step 3: Write AgeGate page**

```tsx
// web/src/pages/AgeGate.tsx
import { useProfile } from "../hooks/useProfile.js";

export function AgeGate() {
  const { confirmAge } = useProfile();

  return (
    <div className="flex flex-col items-center justify-center h-screen bg-black text-white p-8">
      <h1 className="text-3xl font-bold mb-4">Inkognito</h1>
      <p className="text-white/60 text-center mb-8">
        This app contains adult content. You must be 18 or older to continue.
      </p>
      <button
        onClick={() => confirmAge.mutate()}
        disabled={confirmAge.isPending}
        className="bg-violet-600 hover:bg-violet-700 text-white font-bold py-3 px-8 rounded-lg text-lg disabled:opacity-50"
      >
        {confirmAge.isPending ? "..." : "I am 18+"}
      </button>
    </div>
  );
}
```

- [ ] **Step 4: Write CreateProfile page**

```tsx
// web/src/pages/CreateProfile.tsx
import { useState } from "react";
import { useProfile } from "../hooks/useProfile.js";
import { useAuthStore } from "../stores/auth.js";

const GENDERS = [
  { value: "male", label: "Male" },
  { value: "female", label: "Female" },
  { value: "nonbinary", label: "Non-binary" },
  { value: "unset", label: "Prefer not to say" },
];

const INTERESTS = [
  { value: "male", label: "Men" },
  { value: "female", label: "Women" },
  { value: "everyone", label: "Everyone" },
  { value: "random", label: "Random" },
];

export function CreateProfile() {
  const { updateProfile } = useProfile();
  const updateUser = useAuthStore((s) => s.updateUser);
  const [nickname, setNickname] = useState("");
  const [gender, setGender] = useState("unset");
  const [interestedIn, setInterestedIn] = useState("everyone");
  const [error, setError] = useState("");

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    try {
      const result = await updateProfile.mutateAsync({ nickname, gender, interestedIn } as any);
      updateUser(result);
    } catch (err: any) {
      setError(err.message);
    }
  }

  return (
    <div className="flex flex-col items-center justify-center min-h-screen bg-black text-white p-6">
      <h1 className="text-2xl font-bold mb-6">Create Your Profile</h1>
      <form onSubmit={handleSubmit} className="w-full max-w-sm space-y-6">
        <div>
          <label className="block text-sm text-white/60 mb-1">Nickname</label>
          <input
            type="text"
            value={nickname}
            onChange={(e) => setNickname(e.target.value)}
            placeholder="3-30 characters, letters/numbers/_"
            className="w-full bg-white/10 rounded-lg px-4 py-3 text-white placeholder-white/30"
            minLength={3}
            maxLength={30}
            required
          />
        </div>

        <div>
          <label className="block text-sm text-white/60 mb-2">I am</label>
          <div className="grid grid-cols-2 gap-2">
            {GENDERS.map((g) => (
              <button
                key={g.value}
                type="button"
                onClick={() => setGender(g.value)}
                className={`py-2 rounded-lg text-sm ${
                  gender === g.value ? "bg-violet-600" : "bg-white/10"
                }`}
              >
                {g.label}
              </button>
            ))}
          </div>
        </div>

        <div>
          <label className="block text-sm text-white/60 mb-2">Interested in</label>
          <div className="grid grid-cols-2 gap-2">
            {INTERESTS.map((i) => (
              <button
                key={i.value}
                type="button"
                onClick={() => setInterestedIn(i.value)}
                className={`py-2 rounded-lg text-sm ${
                  interestedIn === i.value ? "bg-violet-600" : "bg-white/10"
                }`}
              >
                {i.label}
              </button>
            ))}
          </div>
        </div>

        {error && <p className="text-red-400 text-sm">{error}</p>}

        <button
          type="submit"
          disabled={updateProfile.isPending || nickname.length < 3}
          className="w-full bg-violet-600 hover:bg-violet-700 text-white font-bold py-3 rounded-lg disabled:opacity-50"
        >
          {updateProfile.isPending ? "Saving..." : "Start Chatting"}
        </button>
      </form>
    </div>
  );
}
```

- [ ] **Step 5: Write Home and Settings pages**

```tsx
// web/src/pages/Home.tsx
import { useAuthStore } from "../stores/auth.js";
import { Layout } from "../components/Layout.js";

export function Home() {
  const user = useAuthStore((s) => s.user);

  return (
    <Layout>
      <h1 className="text-2xl font-bold mb-4">Hey, {user?.nickname}</h1>
      <p className="text-white/60 mb-8">Ready to chat?</p>

      <div className="grid grid-cols-2 gap-4">
        <button className="bg-white/10 hover:bg-white/15 rounded-xl p-6 text-left">
          <div className="text-2xl mb-2">🎲</div>
          <div className="font-semibold">Random Match</div>
          <div className="text-sm text-white/50">Find someone new</div>
        </button>
        <button className="bg-white/10 hover:bg-white/15 rounded-xl p-6 text-left">
          <div className="text-2xl mb-2">🔗</div>
          <div className="font-semibold">Invite Link</div>
          <div className="text-sm text-white/50">Chat with someone specific</div>
        </button>
        <button className="bg-white/10 hover:bg-white/15 rounded-xl p-6 text-left">
          <div className="text-2xl mb-2">🔍</div>
          <div className="font-semibold">Discover</div>
          <div className="text-sm text-white/50">Browse available people</div>
        </button>
        <button className="bg-white/10 hover:bg-white/15 rounded-xl p-6 text-left">
          <div className="text-2xl mb-2">👥</div>
          <div className="font-semibold">Group Chat</div>
          <div className="text-sm text-white/50">Join a themed room</div>
        </button>
      </div>
    </Layout>
  );
}
```

```tsx
// web/src/pages/Settings.tsx
import { Layout } from "../components/Layout.js";
import { usePrivacy } from "../hooks/useProfile.js";

function Toggle({ label, value, onChange }: { label: string; value: boolean; onChange: (v: boolean) => void }) {
  return (
    <div className="flex items-center justify-between py-3 border-b border-white/10">
      <span>{label}</span>
      <button
        onClick={() => onChange(!value)}
        className={`w-12 h-6 rounded-full transition-colors ${value ? "bg-violet-600" : "bg-white/20"}`}
      >
        <div className={`w-5 h-5 bg-white rounded-full transition-transform ${value ? "translate-x-6" : "translate-x-0.5"}`} />
      </button>
    </div>
  );
}

export function Settings() {
  const { privacy, updatePrivacy } = usePrivacy();
  const data = privacy.data;

  if (!data) return <Layout><p className="text-white/50">Loading...</p></Layout>;

  function update(key: string, value: boolean) {
    updatePrivacy.mutate({ [key]: value });
  }

  return (
    <Layout>
      <h1 className="text-2xl font-bold mb-6">Privacy Settings</h1>
      <div className="space-y-1">
        <Toggle label="Disappearing Messages" value={data.disappearingMessages} onChange={(v) => update("disappearingMessages", v)} />
        <Toggle label="Screenshot Detection" value={data.screenshotDetection} onChange={(v) => update("screenshotDetection", v)} />
        <Toggle label="Typing Indicator" value={data.typingIndicator} onChange={(v) => update("typingIndicator", v)} />
        <Toggle label="Read Receipts" value={data.readReceipts} onChange={(v) => update("readReceipts", v)} />
        <Toggle label="Show Online Status" value={data.showOnlineStatus} onChange={(v) => update("showOnlineStatus", v)} />
      </div>
    </Layout>
  );
}
```

- [ ] **Step 6: Write App.tsx with routing**

```tsx
// web/src/App.tsx
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { useEffect, useState } from "react";
import { useAuth } from "./hooks/useAuth.js";
import { useAuthStore } from "./stores/auth.js";
import { AgeGate } from "./pages/AgeGate.js";
import { CreateProfile } from "./pages/CreateProfile.js";
import { Home } from "./pages/Home.js";
import { Settings } from "./pages/Settings.js";

function AuthGate({ children }: { children: React.ReactNode }) {
  const { login, isAuthenticated } = useAuth();
  const user = useAuthStore((s) => s.user);
  const isNewUser = useAuthStore((s) => s.isNewUser);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    login().catch(console.error).finally(() => setLoading(false));
  }, [login]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-screen bg-black text-white">
        <p className="text-white/50">Connecting...</p>
      </div>
    );
  }

  if (!isAuthenticated || !user) {
    return (
      <div className="flex items-center justify-center h-screen bg-black text-white">
        <p className="text-red-400">Failed to authenticate. Please open via Telegram.</p>
      </div>
    );
  }

  if (!user.ageConfirmed) return <AgeGate />;
  if (isNewUser || user.nickname.startsWith("user_")) return <CreateProfile />;

  return <>{children}</>;
}

export function App() {
  return (
    <BrowserRouter>
      <AuthGate>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<Navigate to="/" />} />
        </Routes>
      </AuthGate>
    </BrowserRouter>
  );
}
```

- [ ] **Step 7: Verify frontend compiles**

Run: `cd /Users/nemasha/MyProjects/gdpr-agent/sexting/inkognito/web && npx tsc --noEmit`
Expected: No errors.

- [ ] **Step 8: Commit**

```bash
git add inkognito/web/src/
git commit -m "feat: add frontend pages — age gate, profile creation, home, privacy settings"
```

---

### Task 15: Run All Tests & Verify End-to-End

**Files:** None (verification only)

- [ ] **Step 1: Run full backend test suite**

Run: `cd /Users/nemasha/MyProjects/gdpr-agent/sexting/inkognito/server && npx vitest run`
Expected: All tests pass (crypto: 3, telegram: 3, auth: 3, users: 5, privacy: 3 = 17 total).

- [ ] **Step 2: Verify frontend builds**

Run: `cd /Users/nemasha/MyProjects/gdpr-agent/sexting/inkognito/web && npx vite build`
Expected: Build succeeds, output in `dist/`.

- [ ] **Step 3: Verify Docker + server + frontend together**

Run:
```bash
cd /Users/nemasha/MyProjects/gdpr-agent/sexting/inkognito
docker compose up -d
cd server && npx prisma migrate deploy && cd ..
```
Expected: Database migrated successfully.

- [ ] **Step 4: Commit any fixes**

If any test or build issues were found, fix and commit:
```bash
git add -A
git commit -m "fix: resolve integration issues from end-to-end verification"
```

- [ ] **Step 5: Final commit — mark Plan 1 complete**

```bash
git add -A
git commit -m "milestone: complete Plan 1 — foundation, auth, profiles, privacy settings"
```
