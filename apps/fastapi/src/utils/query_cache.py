"""
Query Result Caching Layer for Performance Optimization
Caches frequently accessed data like countries, currencies, and measuring units
"""
from functools import lru_cache
from typing import List, Dict, Any
from sqlalchemy.orm import Session
from models import Country, Currency, MeasuringUnit
import logging

logger = logging.getLogger("apps.fastapi")


class QueryCache:
    """
    Provides caching for frequently accessed database queries that rarely change.
    This reduces database round trips for reference data.
    """

    _countries_cache = None
    _currencies_cache = None
    _measuring_units_cache = None

    @classmethod
    def get_all_countries(cls, db: Session) -> List[Country]:
        """
        Get all countries from cache or database.
        Countries rarely change, so we cache them in memory.
        """
        if cls._countries_cache is None:
            logger.info("[CACHE] Loading countries into cache")
            cls._countries_cache = db.query(Country).all()
        return cls._countries_cache

    @classmethod
    def get_all_currencies(cls, db: Session) -> List[Currency]:
        """
        Get all currencies from cache or database.
        Currencies rarely change, so we cache them in memory.
        """
        if cls._currencies_cache is None:
            logger.info("[CACHE] Loading currencies into cache")
            cls._currencies_cache = db.query(Currency).all()
        return cls._currencies_cache

    @classmethod
    def get_country_by_code(cls, db: Session, code: str) -> Country:
        """
        Get a country by code from cache.
        """
        countries = cls.get_all_countries(db)
        for country in countries:
            if country.code.lower() == code.lower():
                return country
        return None

    @classmethod
    def clear_cache(cls):
        """
        Clear all cached data. Call this when reference data is updated.
        """
        logger.info("[CACHE] Clearing query cache")
        cls._countries_cache = None
        cls._currencies_cache = None
        cls._measuring_units_cache = None


@lru_cache(maxsize=128)
def cached_ingredient_exists(ingredient_id: str) -> bool:
    """
    Check if an ingredient exists. Uses LRU cache.
    Cache size of 128 ingredients should cover most frequent queries.
    """
    # This would need to be implemented with actual DB check
    # For now, placeholder
    return True


@lru_cache(maxsize=256)
def cached_recipe_exists(recipe_id: str) -> bool:
    """
    Check if a recipe exists. Uses LRU cache.
    Cache size of 256 recipes should cover most frequent queries.
    """
    # This would need to be implemented with actual DB check
    # For now, placeholder
    return True


def clear_lru_caches():
    """Clear all LRU caches"""
    cached_ingredient_exists.cache_clear()
    cached_recipe_exists.cache_clear()
    logger.info("[CACHE] Cleared LRU caches")
