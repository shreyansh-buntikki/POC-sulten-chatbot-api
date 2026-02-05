"""
SQLAlchemy Models for Sulten Database
Defines ORM models for ingredient, nutrition, pricing, user tables, and chatbot
"""
from sqlalchemy import Column, String, DateTime, Numeric, Integer, Boolean, Date, ForeignKey, func, Text, Enum as SQLEnum, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from pgvector.sqlalchemy import Vector
import enum
import uuid

Base = declarative_base()


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
    languageId = Column(UUID(as_uuid=True), ForeignKey('language.id'))


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
