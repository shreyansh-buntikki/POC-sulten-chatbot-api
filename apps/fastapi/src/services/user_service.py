"""
User Service - Handles user-related business logic
"""
from typing import List, Optional
from sqlalchemy.orm import Session
from models import User


class UserService:
    """Service class for user operations"""

    @staticmethod
    def list_users(
        db: Session,
        skip: int = 0,
        limit: int = 100,
        role: Optional[str] = None,
        gender: Optional[str] = None,
    ) -> List[User]:
        """
        Get a list of users with optional filters.

        Args:
            db: Database session
            skip: Number of records to skip (for pagination)
            limit: Maximum number of records to return
            role: Filter by user role (optional)
            gender: Filter by gender (optional)

        Returns:
            List of User objects
        """
        query = db.query(User)

        if role:
            query = query.filter(User.role == role)

        if gender:
            query = query.filter(User.gender == gender)

        return query.order_by(User.createdAt.desc()).offset(skip).limit(limit).all()

    @staticmethod
    def get_user_by_uid(db: Session, user_uid: str) -> Optional[User]:
        """
        Get a specific user by UID.

        Args:
            db: Database session
            user_uid: User's UID

        Returns:
            User object or None if not found
        """
        return db.query(User).filter(User.uid == user_uid).first()

    @staticmethod
    def user_to_dict(user: User) -> dict:
        """
        Convert a User object to a dictionary.

        Args:
            user: User object

        Returns:
            Dictionary representation of the user
        """
        return {
            "uid": user.uid,
            "username": user.username,
            "name": user.name,
            "bio": user.bio,
            "image": user.image,
            "gender": user.gender.value if user.gender else None,
            "role": user.role.value if user.role else None,
            "tag": user.tag,
            "dob": user.dob.isoformat() if user.dob else None,
            "createdAt": user.createdAt.isoformat() if user.createdAt else None,
            "lastSeen": user.lastSeen.isoformat() if user.lastSeen else None,
            "termsAccepted": user.termsAccepted,
        }
