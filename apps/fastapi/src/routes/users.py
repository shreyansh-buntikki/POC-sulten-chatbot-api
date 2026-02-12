"""
User Routes - Endpoints for user management
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, status, Query
from sqlalchemy.orm import Session

from apps.fastapi import logger
from database import get_db
from models import User, UserRoleEnum, UserGenderEnum
from apps.fastapi.src.services.user_service import UserService

users_route = APIRouter(prefix="/users", tags=["Users"])


class UserRoutes:
    """Route handlers for user operations"""

    @staticmethod
    @users_route.get(
        "",
        status_code=status.HTTP_200_OK,
        summary="List all users",
        description="Retrieve a paginated list of users with optional filtering by role and gender",
    )
    def list_users(
        skip: int = Query(0, ge=0, description="Number of records to skip"),
        limit: int = Query(100, ge=1, le=1000, description="Maximum number of records to return"),
        role: Optional[UserRoleEnum] = Query(None, description="Filter by user role"),
        gender: Optional[UserGenderEnum] = Query(None, description="Filter by gender"),
        db: Session = Depends(get_db),
    ):
        """
        Get a paginated list of users.

        Args:
            skip: Number of records to skip (for pagination)
            limit: Maximum number of records to return
            role: Optional filter by user role
            gender: Optional filter by gender
            db: Database session

        Returns:
            Dictionary containing success status, list of users, and count
        """
        logger.info(f"Fetching users - skip: {skip}, limit: {limit}, role: {role}, gender: {gender}")

        users = UserService.list_users(db, skip=skip, limit=limit, role=role.value if role else None, gender=gender.value if gender else None)

        return {
            "success": True,
            "status_code": status.HTTP_200_OK,
            "data": {
                "users": [UserService.user_to_dict(user) for user in users],
                "count": len(users),
            },
        }

    @staticmethod
    @users_route.get(
        "/{user_uid}",
        status_code=status.HTTP_200_OK,
        summary="Get user by UID",
        description="Retrieve detailed information about a specific user",
    )
    def get_user(
        user_uid: str,
        db: Session = Depends(get_db),
    ):
        """
        Get a specific user by UID.

        Args:
            user_uid: User's UID
            db: Database session

        Returns:
            Dictionary containing success status and user details
        """
        logger.info(f"Fetching user with UID: {user_uid}")

        user = UserService.get_user_by_uid(db, user_uid)

        if not user:
            return {
                "success": False,
                "status_code": status.HTTP_404_NOT_FOUND,
                "message": "User not found",
            }

        return {
            "success": True,
            "status_code": status.HTTP_200_OK,
            "data": UserService.user_to_dict(user),
        }
