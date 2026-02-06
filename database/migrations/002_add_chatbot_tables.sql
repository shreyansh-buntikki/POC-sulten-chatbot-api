-- Migration: Add Chatbot Tables and Embeddings
-- Description: Adds tables for chat sessions, messages, and embedding columns to existing tables
-- Version: 002

-- Enable pgvector extension for vector similarity search
CREATE EXTENSION IF NOT EXISTS vector;

-- =====================================================
-- Chat Sessions Table
-- One session per conversation, can be anonymous or linked to a user
-- =====================================================
CREATE TABLE IF NOT EXISTS chat_session (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_uid VARCHAR,
    title VARCHAR(255),              -- Auto-generated from first message
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    is_active BOOLEAN DEFAULT TRUE
);

COMMENT ON TABLE chat_session IS 'Chat sessions for user conversations';
COMMENT ON COLUMN chat_session.id IS 'Unique session identifier';
COMMENT ON COLUMN chat_session.user_uid IS 'Optional user UID (null for anonymous sessions)';
COMMENT ON COLUMN chat_session.title IS 'Session title, auto-generated from first message';
COMMENT ON COLUMN chat_session.is_active IS 'Whether the session is currently active';

-- =====================================================
-- Chat Messages Table
-- Stores all messages for context history
-- =====================================================
CREATE TABLE IF NOT EXISTS chat_message (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id UUID NOT NULL REFERENCES chat_session(id) ON DELETE CASCADE,
    role VARCHAR(20) NOT NULL,       -- 'user' or 'assistant'
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    meta JSONB                       -- Store agent info, tokens, intent, etc. (renamed from 'metadata')
);

COMMENT ON TABLE chat_message IS 'Chat messages for conversation history';
COMMENT ON COLUMN chat_message.role IS 'Message role: user or assistant';
COMMENT ON COLUMN chat_message.meta IS 'Additional data: agent, intent, tokens, etc.';

-- =====================================================
-- Add Embedding Columns to Existing Tables
-- =====================================================

-- Add embedding column to recipe table
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'recipe' AND column_name = 'embedding'
    ) THEN
        ALTER TABLE recipe ADD COLUMN embedding vector(1536);
        COMMENT ON COLUMN recipe.embedding IS '1536-dimensional vector for semantic search (OpenAI text-embedding-3-small)';
    END IF;
END $$;

-- Add embedding column to ingredient table
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'ingredient' AND column_name = 'embedding'
    ) THEN
        ALTER TABLE ingredient ADD COLUMN embedding vector(1536);
        COMMENT ON COLUMN ingredient.embedding IS '1536-dimensional vector for semantic search';
    END IF;
END $$;

-- Add embedding column to seasonality table
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'seasonality' AND column_name = 'embedding'
    ) THEN
        ALTER TABLE seasonality ADD COLUMN embedding vector(1536);
        COMMENT ON COLUMN seasonality.embedding IS '1536-dimensional vector for semantic search';
    END IF;
END $$;

-- Add embedding column to tag table
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'tag' AND column_name = 'embedding'
    ) THEN
        ALTER TABLE tag ADD COLUMN embedding vector(1536);
        COMMENT ON COLUMN tag.embedding IS '1536-dimensional vector for semantic search';
    END IF;
END $$;

-- =====================================================
-- Indexes for Vector Similarity Search
-- Using IVFFlat indexing for approximate nearest neighbor search
-- =====================================================

-- Recipe embedding index (only create if column exists and index doesn't)
CREATE INDEX IF NOT EXISTS idx_recipe_embedding_cosine ON recipe USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)
WHERE embedding IS NOT NULL;

-- Ingredient embedding index
CREATE INDEX IF NOT EXISTS idx_ingredient_embedding_cosine ON ingredient USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)
WHERE embedding IS NOT NULL;

-- Seasonality embedding index
CREATE INDEX IF NOT EXISTS idx_seasonality_embedding_cosine ON seasonality USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)
WHERE embedding IS NOT NULL;

-- Tag embedding index
CREATE INDEX IF NOT EXISTS idx_tag_embedding_cosine ON tag USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)
WHERE embedding IS NOT NULL;

-- =====================================================
-- Indexes for Chat Queries
-- =====================================================

-- Fast lookup for message history
CREATE INDEX IF NOT EXISTS idx_chat_message_session ON chat_message(session_id, created_at DESC);

-- Fast lookup for user sessions
CREATE INDEX IF NOT EXISTS idx_chat_session_user ON chat_session(user_uid, created_at DESC) WHERE user_uid IS NOT NULL;

-- Index for active sessions
CREATE INDEX IF NOT EXISTS idx_chat_session_active ON chat_session(is_active, updated_at DESC) WHERE is_active = TRUE;
