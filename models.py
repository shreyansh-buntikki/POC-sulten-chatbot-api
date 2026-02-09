"""
SQLAlchemy Models for Sulten Database
Defines ORM models for ingredient, nutrition, pricing, user tables, and chatbot
"""
from sqlalchemy import Column, String, DateTime, Numeric, Integer, Boolean, Date, ForeignKey, func, Text, Enum as SQLEnum, JSON, BigInteger
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from pgvector.sqlalchemy import Vector
import enum
import uuid

Base = declarative_base()


class Language(Base):
    __tablename__ = 'language'

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    globalName = Column(String)


class UserGenderEnum(str, enum.Enum):
    male = "male"
    female = "female"
    other = "other"


class UserRoleEnum(str, enum.Enum):
    admin = "admin"
    vip = "vip"
    team_sulten = "team_sulten"
    verified = "verified"
    community = "community"


class Country(Base):
    __tablename__ = 'country'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code = Column(String(3), unique=True, nullable=False)
    name = Column(String(100), nullable=False)
    createdAt = Column(DateTime(timezone=True), server_default=func.now())
    updatedAt = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Currency(Base):
    __tablename__ = 'currency'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code = Column(String(3), unique=True, nullable=False)
    name = Column(String(50), nullable=False)
    symbol = Column(String(10), nullable=False)
    createdAt = Column(DateTime(timezone=True), server_default=func.now())
    updatedAt = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class MeasuringUnit(Base):
    __tablename__ = 'measuring_unit'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    createdAt = Column(DateTime(timezone=True), server_default=func.now())
    updatedAt = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Ingredient(Base):
    __tablename__ = 'ingredient'

    id = Column(UUID(as_uuid=True), primary_key=True)
    name = Column(String, nullable=False)
    createdAt = Column(DateTime(timezone=True), server_default=func.now())
    updatedAt = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    languageId = Column(String, ForeignKey('language.id'))
    embedding = Column(Vector(1536), nullable=True)  # For semantic search


class IngredientMacros(Base):
    __tablename__ = 'ingredient_macros'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ingredientId = Column(UUID(as_uuid=True), ForeignKey('ingredient.id', ondelete='CASCADE'), nullable=False, unique=True)
    servingSize = Column(Integer, default=100, nullable=False)
    energyKcal = Column(Numeric(10, 2))
    energyKj = Column(Numeric(10, 2))
    protein = Column(Numeric(10, 2))
    carbohydrates = Column(Numeric(10, 2))
    totalFiber = Column(Numeric(10, 2))
    solubleFiber = Column(Numeric(10, 2))
    insolubleFiber = Column(Numeric(10, 2))
    totalSugars = Column(Numeric(10, 2))
    addedSugar = Column(Numeric(10, 2))
    starch = Column(Numeric(10, 2))
    totalFat = Column(Numeric(10, 2))
    saturatedFat = Column(Numeric(10, 2))
    transFat = Column(Numeric(10, 2))
    monounsaturatedFat = Column(Numeric(10, 2))
    polyunsaturatedFat = Column(Numeric(10, 2))
    cholesterol = Column(Numeric(10, 2))
    createdAt = Column(DateTime(timezone=True), server_default=func.now())
    updatedAt = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class IngredientMicros(Base):
    __tablename__ = 'ingredient_micros'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ingredientId = Column(UUID(as_uuid=True), ForeignKey('ingredient.id', ondelete='CASCADE'), nullable=False, unique=True)
    servingSize = Column(Integer, default=100, nullable=False)
    # Vitamins
    vitaminA = Column(Numeric(10, 2))
    vitaminC = Column(Numeric(10, 2))
    vitaminD = Column(Numeric(10, 2))
    vitaminE = Column(Numeric(10, 2))
    vitaminK = Column(Numeric(10, 2))
    thiamineB1 = Column(Numeric(10, 2))
    riboflavinB2 = Column(Numeric(10, 2))
    niacinB3 = Column(Numeric(10, 2))
    pantothenicAcidB5 = Column(Numeric(10, 2))
    vitaminB6 = Column(Numeric(10, 2))
    biotinB7 = Column(Numeric(10, 2))
    folateB9 = Column(Numeric(10, 2))
    vitaminB12 = Column(Numeric(10, 2))
    choline = Column(Numeric(10, 2))
    # Minerals
    calcium = Column(Numeric(10, 2))
    iron = Column(Numeric(10, 2))
    magnesium = Column(Numeric(10, 2))
    phosphorus = Column(Numeric(10, 2))
    potassium = Column(Numeric(10, 2))
    sodium = Column(Numeric(10, 2))
    zinc = Column(Numeric(10, 2))
    copper = Column(Numeric(10, 2))
    manganese = Column(Numeric(10, 2))
    selenium = Column(Numeric(10, 2))
    fluoride = Column(Numeric(10, 2))
    createdAt = Column(DateTime(timezone=True), server_default=func.now())
    updatedAt = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class IngredientPricing(Base):
    __tablename__ = 'ingredient_pricing'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ingredientId = Column(UUID(as_uuid=True), ForeignKey('ingredient.id', ondelete='CASCADE'), nullable=False)
    countryId = Column(UUID(as_uuid=True), ForeignKey('country.id', ondelete='CASCADE'), nullable=False)
    currencyId = Column(UUID(as_uuid=True), ForeignKey('currency.id', ondelete='CASCADE'), nullable=False)
    pricePerUnit = Column(Numeric(10, 2), nullable=False)
    quantity = Column(Integer, nullable=False)
    measuringUnitId = Column(UUID(as_uuid=True), ForeignKey('measuring_unit.id', ondelete='RESTRICT'), nullable=False)
    createdAt = Column(DateTime(timezone=True), server_default=func.now())
    updatedAt = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SeasonalityTypeEnum(str, enum.Enum):
    WEATHER = "WEATHER"
    FESTIVAL = "FESTIVAL"
    INGREDIENT_AVAILABILITY = "INGREDIENT_AVAILABILITY"
    CULTURAL_OCCASION = "CULTURAL_OCCASION"
    DIETARY_PRACTICE = "DIETARY_PRACTICE"
    MEAL_TIMING = "MEAL_TIMING"
    LIFESTYLE = "LIFESTYLE"
    REGIONAL = "REGIONAL"
    HEALTH_CYCLE = "HEALTH_CYCLE"


class Seasonality(Base):
    __tablename__ = 'seasonality'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    type = Column(String(50), nullable=False)
    isActive = Column(Boolean, default=True, nullable=False)
    createdAt = Column(DateTime(timezone=True), server_default=func.now())
    updatedAt = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SeasonalityTranslation(Base):
    __tablename__ = 'seasonality_translation'

    id = Column(Integer, primary_key=True, autoincrement=False)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    languageId = Column(String(2), ForeignKey('language.id'), nullable=False)
    seasonalityId = Column(UUID(as_uuid=True), ForeignKey('seasonality.id', ondelete='CASCADE'), nullable=False)
    createdAt = Column(DateTime(timezone=True), server_default=func.now())
    updatedAt = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class RecipeSeasonality(Base):
    __tablename__ = 'recipe_seasonality'

    recipeId = Column(UUID(as_uuid=True), ForeignKey('recipe.id', ondelete='CASCADE'), primary_key=True)
    seasonalityId = Column(UUID(as_uuid=True), ForeignKey('seasonality.id', ondelete='CASCADE'), primary_key=True)
    createdAt = Column(DateTime(timezone=True), server_default=func.now())


# =====================================================
# Recipe Models
# =====================================================

class Recipe(Base):
    __tablename__ = 'recipe'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String)
    slug = Column(String)
    ingress = Column(String)
    image = Column(String)
    createdAt = Column(DateTime(timezone=True), server_default=func.now())
    updatedAt = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    publishedAt = Column(DateTime(timezone=True))
    status = Column(String, nullable=False, default='draft')
    difficulty = Column(String, nullable=False, default='medium')
    servings = Column(Integer)
    prepTime = Column(Integer)
    cookTime = Column(Integer)
    userUid = Column(String, ForeignKey('user.uid'))
    languageId = Column(String, ForeignKey('language.id'))
    deletedAt = Column(DateTime(timezone=True))
    private = Column(Boolean, nullable=False, default=False)
    search_vector = Column(Text)  # tsvector (fixed name)
    embedding = Column(Vector(1536), nullable=True)  # For semantic search


class RecipeIngredient(Base):
    __tablename__ = 'recipe_ingredient'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    amount = Column(Integer)
    section = Column(String)
    unitId = Column(UUID(as_uuid=True), ForeignKey('measuring_unit.id', ondelete='CASCADE'))
    ingredientId = Column(UUID(as_uuid=True), ForeignKey('ingredient.id', ondelete='CASCADE'))
    recipeId = Column(UUID(as_uuid=True), ForeignKey('recipe.id', ondelete='CASCADE'))
    createdAt = Column(DateTime(timezone=True), server_default=func.now())
    updatedAt = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    deletedAt = Column(DateTime(timezone=True))
    order = Column(Integer)


class RecipeInstruction(Base):
    __tablename__ = 'recipe_instruction'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    description = Column(String)
    createdAt = Column(DateTime(timezone=True), server_default=func.now())
    updatedAt = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    image = Column(String)
    recipeId = Column(UUID(as_uuid=True), ForeignKey('recipe.id', ondelete='CASCADE'))
    deletedAt = Column(DateTime(timezone=True))
    order = Column(Integer)


class Tag(Base):
    __tablename__ = 'tag'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False, unique=True)


class RecipeTagsTag(Base):
    """Recipe-Tag junction table"""
    __tablename__ = 'recipe_tags_tag'

    recipeId = Column(UUID(as_uuid=True), ForeignKey('recipe.id', ondelete='CASCADE'), primary_key=True)
    tagId = Column(UUID(as_uuid=True), ForeignKey('tag.id', ondelete='CASCADE'), primary_key=True)


class User(Base):
    __tablename__ = 'user'

    uid = Column(String, primary_key=True)
    username = Column(String)
    bio = Column(String)
    createdAt = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    lastSeen = Column(DateTime(timezone=True), server_default=func.now())
    name = Column(String)
    image = Column(String)
    dob = Column(DateTime(timezone=True))
    gender = Column(SQLEnum(UserGenderEnum))
    messagingTokens = Column(Text)
    termsAccepted = Column(Boolean, default=True)
    role = Column(SQLEnum(UserRoleEnum), default=UserRoleEnum.community, nullable=False)
    tag = Column(String, default='community')


# =====================================================
# Chatbot Models
# =====================================================

class ChatSession(Base):
    """Chat session for tracking user conversations"""
    __tablename__ = 'chat_session'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_uid = Column(String, nullable=True)  # Optional - for anonymous sessions
    title = Column(String(255))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    is_active = Column(Boolean, default=True)


class ChatMessageRoleEnum(str, enum.Enum):
    """Chat message role enum"""
    user = "user"
    assistant = "assistant"
    system = "system"


class ChatMessage(Base):
    """Individual chat messages for context history"""
    __tablename__ = 'chat_message'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(UUID(as_uuid=True), ForeignKey('chat_session.id', ondelete='CASCADE'), nullable=False)
    role = Column(String(20), nullable=False)  # 'user', 'assistant', 'system'
    content = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    meta = Column(JSON)  # Stores agent info, tokens, intent, etc. (renamed from 'metadata' - reserved in SQLAlchemy)


# =====================================================
# User Interaction Models
# =====================================================

class UserLikesRecipe(Base):
    """User likes recipe relationship"""
    __tablename__ = 'user_likes_recipe'

    userUid = Column(String, ForeignKey('user.uid', ondelete='CASCADE'), primary_key=True)
    recipeId = Column(UUID(as_uuid=True), ForeignKey('recipe.id', ondelete='CASCADE'), primary_key=True)


class Like(Base):
    """Generic like table for various entities"""
    __tablename__ = 'like'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    userId = Column(String, ForeignKey('user.uid'), nullable=False)
    targetType = Column(String, nullable=False)  # e.g., 'recipe', 'comment'
    targetId = Column(UUID(as_uuid=True), nullable=False)
    createdAt = Column(DateTime(timezone=True), server_default=func.now())


class UserPurchase(Base):
    """User purchases (bundles, recipes, etc.)"""
    __tablename__ = 'user_purchase'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    transactionId = Column(String, nullable=False)
    purchaseType = Column(String, nullable=False)  # enum: bundle, recipe, etc.
    platform = Column(String, nullable=False)  # enum: ios, android, web
    purchasedAt = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    userUid = Column(String, ForeignKey('user.uid'))
    bundleId = Column(UUID(as_uuid=True), ForeignKey('bundle.id'))
    purchasedAtMillis = Column(BigInteger)


class Bundle(Base):
    """Bundle of recipes sold together"""
    __tablename__ = 'bundle'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    createdAt = Column(DateTime(timezone=True), server_default=func.now())
    updatedAt = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    name = Column(String, nullable=False)
    ingress = Column(Text, nullable=False)
    isActive = Column(Boolean, nullable=False, default=False)
    image = Column(String)
    userUid = Column(String, ForeignKey('user.uid'))
    pricingDetailId = Column(UUID(as_uuid=True), ForeignKey('bundle_price.id'))
    deletedAt = Column(DateTime(timezone=True))


class BundlePrice(Base):
    """Bundle pricing by country/currency"""
    __tablename__ = 'bundle_price'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bundleId = Column(UUID(as_uuid=True), ForeignKey('bundle.id', ondelete='CASCADE'), nullable=False)
    countryId = Column(UUID(as_uuid=True), ForeignKey('country.id', ondelete='CASCADE'), nullable=False)
    currencyId = Column(UUID(as_uuid=True), ForeignKey('currency.id', ondelete='CASCADE'), nullable=False)
    price = Column(Numeric(10, 2), nullable=False)
    createdAt = Column(DateTime(timezone=True), server_default=func.now())
    updatedAt = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class BundleRecipe(Base):
    """Recipes included in a bundle"""
    __tablename__ = 'bundle_recipe'

    bundleId = Column(UUID(as_uuid=True), ForeignKey('bundle.id', ondelete='CASCADE'), primary_key=True)
    recipeId = Column(UUID(as_uuid=True), ForeignKey('recipe.id', ondelete='CASCADE'), primary_key=True)
    order = Column(Integer, nullable=False)
    isFree = Column(Boolean, default=False, nullable=False)
    deletedAt = Column(DateTime(timezone=True))
