# Auto-generated schema data
# Generated from database

SCHEMA_DATA = {
    "bundle": {
        "columns": [
            {
                "name": "id",
                "type": "uuid"
            },
            {
                "name": "createdAt",
                "type": "timestamp without time zone"
            },
            {
                "name": "updatedAt",
                "type": "timestamp without time zone"
            },
            {
                "name": "name",
                "type": "character varying"
            },
            {
                "name": "ingress",
                "type": "text"
            },
            {
                "name": "isActive",
                "type": "boolean"
            },
            {
                "name": "image",
                "type": "character varying"
            },
            {
                "name": "userUid",
                "type": "character varying"
            },
            {
                "name": "pricingDetailId",
                "type": "uuid"
            },
            {
                "name": "deletedAt",
                "type": "timestamp without time zone"
            }
        ]
    },
    "bundle_price": {
        "columns": [
            {
                "name": "id",
                "type": "uuid"
            },
            {
                "name": "price",
                "type": "integer"
            },
            {
                "name": "iosName",
                "type": "character varying"
            },
            {
                "name": "androidName",
                "type": "character varying"
            },
            {
                "name": "name",
                "type": "character varying"
            }
        ]
    },
    "bundle_recipe": {
        "columns": [
            {
                "name": "bundleId",
                "type": "uuid"
            },
            {
                "name": "recipeId",
                "type": "uuid"
            },
            {
                "name": "order",
                "type": "integer"
            },
            {
                "name": "isFree",
                "type": "boolean"
            },
            {
                "name": "deletedAt",
                "type": "timestamp without time zone"
            }
        ]
    },
    "chat_message": {
        "columns": [
            {
                "name": "id",
                "type": "uuid"
            },
            {
                "name": "session_id",
                "type": "uuid"
            },
            {
                "name": "role",
                "type": "character varying"
            },
            {
                "name": "content",
                "type": "text"
            },
            {
                "name": "created_at",
                "type": "timestamp without time zone"
            },
            {
                "name": "meta",
                "type": "jsonb"
            }
        ]
    },
    "chat_session": {
        "columns": [
            {
                "name": "id",
                "type": "uuid"
            },
            {
                "name": "user_uid",
                "type": "character varying"
            },
            {
                "name": "title",
                "type": "character varying"
            },
            {
                "name": "created_at",
                "type": "timestamp without time zone"
            },
            {
                "name": "updated_at",
                "type": "timestamp without time zone"
            },
            {
                "name": "is_active",
                "type": "boolean"
            }
        ]
    },
    "country": {
        "columns": [
            {
                "name": "id",
                "type": "uuid"
            },
            {
                "name": "code",
                "type": "character varying"
            },
            {
                "name": "name",
                "type": "character varying"
            },
            {
                "name": "createdAt",
                "type": "timestamp without time zone"
            },
            {
                "name": "updatedAt",
                "type": "timestamp without time zone"
            }
        ]
    },
    "currency": {
        "columns": [
            {
                "name": "id",
                "type": "uuid"
            },
            {
                "name": "code",
                "type": "character varying"
            },
            {
                "name": "name",
                "type": "character varying"
            },
            {
                "name": "symbol",
                "type": "character varying"
            },
            {
                "name": "createdAt",
                "type": "timestamp without time zone"
            },
            {
                "name": "updatedAt",
                "type": "timestamp without time zone"
            }
        ]
    },
    "ingredient": {
        "columns": [
            {
                "name": "id",
                "type": "uuid"
            },
            {
                "name": "name",
                "type": "character varying"
            },
            {
                "name": "createdAt",
                "type": "timestamp without time zone"
            },
            {
                "name": "updatedAt",
                "type": "timestamp without time zone"
            },
            {
                "name": "languageId",
                "type": "character varying"
            },
            {
                "name": "embedding",
                "type": "USER-DEFINED"
            }
        ]
    },
    "ingredient_macros": {
        "columns": [
            {
                "name": "id",
                "type": "uuid"
            },
            {
                "name": "ingredientId",
                "type": "uuid"
            },
            {
                "name": "servingSize",
                "type": "integer"
            },
            {
                "name": "energyKcal",
                "type": "numeric"
            },
            {
                "name": "energyKj",
                "type": "numeric"
            },
            {
                "name": "protein",
                "type": "numeric"
            },
            {
                "name": "carbohydrates",
                "type": "numeric"
            },
            {
                "name": "totalFiber",
                "type": "numeric"
            },
            {
                "name": "solubleFiber",
                "type": "numeric"
            },
            {
                "name": "insolubleFiber",
                "type": "numeric"
            },
            {
                "name": "totalSugars",
                "type": "numeric"
            },
            {
                "name": "addedSugar",
                "type": "numeric"
            },
            {
                "name": "starch",
                "type": "numeric"
            },
            {
                "name": "totalFat",
                "type": "numeric"
            },
            {
                "name": "saturatedFat",
                "type": "numeric"
            },
            {
                "name": "transFat",
                "type": "numeric"
            },
            {
                "name": "monounsaturatedFat",
                "type": "numeric"
            },
            {
                "name": "polyunsaturatedFat",
                "type": "numeric"
            },
            {
                "name": "cholesterol",
                "type": "numeric"
            },
            {
                "name": "createdAt",
                "type": "timestamp without time zone"
            },
            {
                "name": "updatedAt",
                "type": "timestamp without time zone"
            }
        ]
    },
    "ingredient_micros": {
        "columns": [
            {
                "name": "id",
                "type": "uuid"
            },
            {
                "name": "ingredientId",
                "type": "uuid"
            },
            {
                "name": "servingSize",
                "type": "integer"
            },
            {
                "name": "vitaminA",
                "type": "numeric"
            },
            {
                "name": "vitaminC",
                "type": "numeric"
            },
            {
                "name": "vitaminD",
                "type": "numeric"
            },
            {
                "name": "vitaminE",
                "type": "numeric"
            },
            {
                "name": "vitaminK",
                "type": "numeric"
            },
            {
                "name": "thiamineB1",
                "type": "numeric"
            },
            {
                "name": "riboflavinB2",
                "type": "numeric"
            },
            {
                "name": "niacinB3",
                "type": "numeric"
            },
            {
                "name": "pantothenicAcidB5",
                "type": "numeric"
            },
            {
                "name": "vitaminB6",
                "type": "numeric"
            },
            {
                "name": "biotinB7",
                "type": "numeric"
            },
            {
                "name": "folateB9",
                "type": "numeric"
            },
            {
                "name": "vitaminB12",
                "type": "numeric"
            },
            {
                "name": "choline",
                "type": "numeric"
            },
            {
                "name": "calcium",
                "type": "numeric"
            },
            {
                "name": "iron",
                "type": "numeric"
            },
            {
                "name": "magnesium",
                "type": "numeric"
            },
            {
                "name": "phosphorus",
                "type": "numeric"
            },
            {
                "name": "potassium",
                "type": "numeric"
            },
            {
                "name": "sodium",
                "type": "numeric"
            },
            {
                "name": "zinc",
                "type": "numeric"
            },
            {
                "name": "copper",
                "type": "numeric"
            },
            {
                "name": "manganese",
                "type": "numeric"
            },
            {
                "name": "selenium",
                "type": "numeric"
            },
            {
                "name": "fluoride",
                "type": "numeric"
            },
            {
                "name": "createdAt",
                "type": "timestamp without time zone"
            },
            {
                "name": "updatedAt",
                "type": "timestamp without time zone"
            }
        ]
    },
    "ingredient_pricing": {
        "columns": [
            {
                "name": "id",
                "type": "uuid"
            },
            {
                "name": "ingredientId",
                "type": "uuid"
            },
            {
                "name": "countryId",
                "type": "uuid"
            },
            {
                "name": "currencyId",
                "type": "uuid"
            },
            {
                "name": "pricePerUnit",
                "type": "numeric"
            },
            {
                "name": "quantity",
                "type": "integer"
            },
            {
                "name": "measuringUnitId",
                "type": "uuid"
            },
            {
                "name": "createdAt",
                "type": "timestamp without time zone"
            },
            {
                "name": "updatedAt",
                "type": "timestamp without time zone"
            }
        ]
    },
    "language": {
        "columns": [
            {
                "name": "id",
                "type": "character varying"
            },
            {
                "name": "name",
                "type": "character varying"
            },
            {
                "name": "globalName",
                "type": "character varying"
            }
        ]
    },
    "like": {
        "columns": [
            {
                "name": "id",
                "type": "uuid"
            },
            {
                "name": "entityId",
                "type": "character varying"
            },
            {
                "name": "entityType",
                "type": "character varying"
            },
            {
                "name": "createdAt",
                "type": "timestamp without time zone"
            },
            {
                "name": "userUid",
                "type": "character varying"
            }
        ]
    },
    "measuring_unit": {
        "columns": [
            {
                "name": "id",
                "type": "uuid"
            },
            {
                "name": "createdAt",
                "type": "timestamp without time zone"
            },
            {
                "name": "updatedAt",
                "type": "timestamp without time zone"
            }
        ]
    },
    "measuring_unit_translation": {
        "columns": [
            {
                "name": "id",
                "type": "integer"
            },
            {
                "name": "name",
                "type": "character varying"
            },
            {
                "name": "longName",
                "type": "character varying"
            },
            {
                "name": "languageId",
                "type": "character varying"
            },
            {
                "name": "measuringUnitId",
                "type": "uuid"
            }
        ]
    },
    "migrations": {
        "columns": [
            {
                "name": "id",
                "type": "integer"
            },
            {
                "name": "timestamp",
                "type": "bigint"
            },
            {
                "name": "name",
                "type": "character varying"
            }
        ]
    },
    "recipe": {
        "columns": [
            {
                "name": "id",
                "type": "uuid"
            },
            {
                "name": "name",
                "type": "character varying"
            },
            {
                "name": "slug",
                "type": "character varying"
            },
            {
                "name": "ingress",
                "type": "character varying"
            },
            {
                "name": "image",
                "type": "character varying"
            },
            {
                "name": "createdAt",
                "type": "timestamp without time zone"
            },
            {
                "name": "updatedAt",
                "type": "timestamp without time zone"
            },
            {
                "name": "publishedAt",
                "type": "timestamp with time zone"
            },
            {
                "name": "status",
                "type": "character varying"
            },
            {
                "name": "difficulty",
                "type": "character varying"
            },
            {
                "name": "servings",
                "type": "integer"
            },
            {
                "name": "prepTime",
                "type": "integer"
            },
            {
                "name": "cookTime",
                "type": "integer"
            },
            {
                "name": "userUid",
                "type": "character varying"
            },
            {
                "name": "languageId",
                "type": "character varying"
            },
            {
                "name": "deletedAt",
                "type": "timestamp without time zone"
            },
            {
                "name": "private",
                "type": "boolean"
            },
            {
                "name": "search_vector",
                "type": "tsvector"
            },
            {
                "name": "embedding",
                "type": "USER-DEFINED"
            }
        ]
    },
    "recipe_ingredient": {
        "columns": [
            {
                "name": "id",
                "type": "uuid"
            },
            {
                "name": "amount",
                "type": "double precision"
            },
            {
                "name": "section",
                "type": "character varying"
            },
            {
                "name": "unitId",
                "type": "uuid"
            },
            {
                "name": "ingredientId",
                "type": "uuid"
            },
            {
                "name": "recipeId",
                "type": "uuid"
            },
            {
                "name": "createdAt",
                "type": "timestamp without time zone"
            },
            {
                "name": "updatedAt",
                "type": "timestamp without time zone"
            },
            {
                "name": "deletedAt",
                "type": "timestamp without time zone"
            },
            {
                "name": "order",
                "type": "integer"
            }
        ]
    },
    "recipe_instruction": {
        "columns": [
            {
                "name": "id",
                "type": "uuid"
            },
            {
                "name": "description",
                "type": "character varying"
            },
            {
                "name": "createdAt",
                "type": "timestamp without time zone"
            },
            {
                "name": "updatedAt",
                "type": "timestamp without time zone"
            },
            {
                "name": "image",
                "type": "character varying"
            },
            {
                "name": "recipeId",
                "type": "uuid"
            },
            {
                "name": "deletedAt",
                "type": "timestamp without time zone"
            },
            {
                "name": "order",
                "type": "integer"
            }
        ]
    },
    "recipe_instruction_selected_ingredients_ingredient": {
        "columns": [
            {
                "name": "recipeInstructionId",
                "type": "uuid"
            },
            {
                "name": "ingredientId",
                "type": "uuid"
            }
        ]
    },
    "recipe_recipe_types_recipe_type": {
        "columns": [
            {
                "name": "recipeId",
                "type": "uuid"
            },
            {
                "name": "recipeTypeId",
                "type": "uuid"
            }
        ]
    },
    "recipe_seasonality": {
        "columns": [
            {
                "name": "recipeId",
                "type": "uuid"
            },
            {
                "name": "seasonalityId",
                "type": "uuid"
            },
            {
                "name": "createdAt",
                "type": "timestamp without time zone"
            }
        ]
    },
    "recipe_tags_tag": {
        "columns": [
            {
                "name": "recipeId",
                "type": "uuid"
            },
            {
                "name": "tagId",
                "type": "uuid"
            }
        ]
    },
    "recipe_type": {
        "columns": [
            {
                "name": "id",
                "type": "uuid"
            },
            {
                "name": "createdAt",
                "type": "timestamp without time zone"
            },
            {
                "name": "updatedAt",
                "type": "timestamp without time zone"
            }
        ]
    },
    "recipe_type_translation": {
        "columns": [
            {
                "name": "id",
                "type": "integer"
            },
            {
                "name": "name",
                "type": "character varying"
            },
            {
                "name": "languageId",
                "type": "character varying"
            },
            {
                "name": "recipeTypeId",
                "type": "uuid"
            }
        ]
    },
    "seasonality": {
        "columns": [
            {
                "name": "id",
                "type": "uuid"
            },
            {
                "name": "type",
                "type": "character varying"
            },
            {
                "name": "isActive",
                "type": "boolean"
            },
            {
                "name": "createdAt",
                "type": "timestamp without time zone"
            },
            {
                "name": "updatedAt",
                "type": "timestamp without time zone"
            },
            {
                "name": "embedding",
                "type": "USER-DEFINED"
            }
        ]
    },
    "seasonality_translation": {
        "columns": [
            {
                "name": "id",
                "type": "integer"
            },
            {
                "name": "name",
                "type": "character varying"
            },
            {
                "name": "description",
                "type": "text"
            },
            {
                "name": "languageId",
                "type": "character varying"
            },
            {
                "name": "seasonalityId",
                "type": "uuid"
            },
            {
                "name": "createdAt",
                "type": "timestamp without time zone"
            },
            {
                "name": "updatedAt",
                "type": "timestamp without time zone"
            }
        ]
    },
    "tag": {
        "columns": [
            {
                "name": "id",
                "type": "uuid"
            },
            {
                "name": "name",
                "type": "character varying"
            },
            {
                "name": "embedding",
                "type": "USER-DEFINED"
            }
        ]
    },
    "user": {
        "columns": [
            {
                "name": "uid",
                "type": "character varying"
            },
            {
                "name": "username",
                "type": "character varying"
            },
            {
                "name": "bio",
                "type": "character varying"
            },
            {
                "name": "createdAt",
                "type": "timestamp without time zone"
            },
            {
                "name": "lastSeen",
                "type": "timestamp with time zone"
            },
            {
                "name": "name",
                "type": "character varying"
            },
            {
                "name": "image",
                "type": "character varying"
            },
            {
                "name": "dob",
                "type": "timestamp with time zone"
            },
            {
                "name": "gender",
                "type": "USER-DEFINED"
            },
            {
                "name": "messagingTokens",
                "type": "text"
            },
            {
                "name": "termsAccepted",
                "type": "boolean"
            },
            {
                "name": "role",
                "type": "USER-DEFINED"
            },
            {
                "name": "tag",
                "type": "character varying"
            }
        ]
    },
    "user_likes_recipe": {
        "columns": [
            {
                "name": "userUid",
                "type": "character varying"
            },
            {
                "name": "recipeId",
                "type": "uuid"
            }
        ]
    },
    "user_purchase": {
        "columns": [
            {
                "name": "id",
                "type": "uuid"
            },
            {
                "name": "transactionId",
                "type": "character varying"
            },
            {
                "name": "purchaseType",
                "type": "USER-DEFINED"
            },
            {
                "name": "platform",
                "type": "USER-DEFINED"
            },
            {
                "name": "purchasedAt",
                "type": "timestamp without time zone"
            },
            {
                "name": "userUid",
                "type": "character varying"
            },
            {
                "name": "bundleId",
                "type": "uuid"
            },
            {
                "name": "purchasedAtMillis",
                "type": "bigint"
            }
        ]
    }
}
