# Database Schema for SQL Query Generation
## Database: sulten-db (Schema: public)

This schema describes a recipe and meal management platform with users, recipes, ingredients,
bundles (collections of recipes for purchase), and various supporting tables.

## Tables:

### `chat_session`
> Chat session for tracking user conversations in the chatbot

| Column | Type | Description |
|--------|------|-------------|
| `id` **PK** | `UUID` **NOT NULL** | Unique session identifier |
| `user_uid` | `VARCHAR` | Optional user UID (null for anonymous sessions) |
| `title` | `VARCHAR` | Session title, auto-generated from first message |
| `created_at` | `TIMESTAMP` **NOT NULL** | Timestamp when session was created |
| `updated_at` | `TIMESTAMP` **NOT NULL** | Timestamp when session was last updated |
| `is_active` | `BOOLEAN` **NOT NULL** | Whether the session is currently active |


### `chat_message`
> Individual chat messages for conversation history

| Column | Type | Description |
|--------|------|-------------|
| `id` **PK** | `UUID` **NOT NULL** | Unique message identifier |
| `session_id` **FK** | `UUID` **NOT NULL** | Reference to chat session → `chat_session.id` |
| `role` | `VARCHAR` **NOT NULL** | Message role: 'user', 'assistant', or 'system' |
| `content` | `TEXT` **NOT NULL** | Message content |
| `created_at` | `TIMESTAMP` **NOT NULL** | Timestamp when message was created |
| `meta` | `JSONB` | Additional metadata: agent, intent, tokens, etc. |


### `recipe`
> Recipe details including name, ingredients, instructions, and metadata

| Column | Type | Description |
|--------|------|-------------|
| `id` **PK** | `UUID` **NOT NULL** | Unique identifier (primary key) |
| `name` | `VARCHAR` | Recipe name |
| `slug` | `VARCHAR` | URL-friendly identifier for SEO |
| `ingress` | `VARCHAR` | Short summary or introduction text |
| `image` | `VARCHAR` | URL/path to recipe image |
| `createdAt` | `TIMESTAMP` **NOT NULL** | Timestamp when record was created |
| `updatedAt` | `TIMESTAMP` **NOT NULL** | Timestamp when record was last updated |
| `publishedAt` | `TIMESTAMP` | Timestamp when content was published |
| `status` | `VARCHAR` **NOT NULL** | Current status (draft, published, etc.) |
| `difficulty` | `VARCHAR` **NOT NULL** | Recipe difficulty level (easy, medium, hard) |
| `servings` | `INTEGER` | Number of servings the recipe yields |
| `prepTime` | `INTEGER` | Preparation time in minutes |
| `cookTime` | `INTEGER` | Cooking time in minutes |
| `userUid` | `VARCHAR` | Reference to user → `user.uid` |
| `languageId` | `VARCHAR` | Reference to language → `language.id` |
| `deletedAt` | `TIMESTAMP` | Soft delete timestamp (null if not deleted) |
| `private` | `BOOLEAN` **NOT NULL** | Whether content is private (not public) |
| `search_vector` | `TSVECTOR` | Full-text search vector for fast searching |
| `embedding` | `VECTOR(1536)` | Vector embedding for semantic search (OpenAI text-embedding-3-small) |


### `recipe_ingredient`
> Junction table linking recipes to their ingredients with amounts

| Column | Type | Description |
|--------|------|-------------|
| `id` **PK** | `UUID` **NOT NULL** | Unique identifier (primary key) |
| `amount` | `DOUBLE PRECISION` | Quantity or amount of ingredient |
| `section` | `VARCHAR` | Recipe section the ingredient belongs to |
| `unitId` | `UUID` | Reference to measuring unit → `measuring_unit.id` |
| `ingredientId` | `UUID` | Reference to ingredient → `ingredient.id` |
| `recipeId` | `UUID` | Reference to recipe → `recipe.id` |
| `createdAt` | `TIMESTAMP` **NOT NULL** | Timestamp when record was created |
| `updatedAt` | `TIMESTAMP` **NOT NULL** | Timestamp when record was last updated |
| `deletedAt` | `TIMESTAMP` | Soft delete timestamp (null if not deleted) |
| `order` | `INTEGER` | Sorting order for display |


### `recipe_instruction`
> Step-by-step cooking instructions for recipes

| Column | Type | Description |
|--------|------|-------------|
| `id` **PK** | `UUID` **NOT NULL** | Unique identifier (primary key) |
| `description` | `VARCHAR` | Text description or instruction |
| `createdAt` | `TIMESTAMP` **NOT NULL** | Timestamp when record was created |
| `updatedAt` | `TIMESTAMP` **NOT NULL** | Timestamp when record was last updated |
| `image` | `VARCHAR` | URL/path to instruction image |
| `recipeId` | `UUID` | Reference to recipe → `recipe.id` |
| `deletedAt` | `TIMESTAMP` | Soft delete timestamp (null if not deleted) |
| `order` | `INTEGER` | Sorting order for display |


### `recipe_instruction_selected_ingredients_ingredient`
> Ingredients highlighted in recipe instructions

| Column | Type | Description |
|--------|------|-------------|
| `recipeInstructionId` **PK** | `UUID` **NOT NULL** | Reference to recipe instruction → `recipe_instruction.id` |
| `ingredientId` **PK** | `UUID` **NOT NULL** | Reference to ingredient → `ingredient.id` |


### `recipe_seasonality`
> Seasonal availability information for recipes

| Column | Type | Description |
|--------|------|-------------|
| `recipeId` **PK** | `UUID` **NOT NULL** | Reference to recipe → `recipe.id` |
| `seasonalityId` **PK** | `UUID` **NOT NULL** | Reference to seasonality → `seasonality.id` |
| `createdAt` | `TIMESTAMP` **NOT NULL** | Timestamp when record was created |


### `recipe_tags_tag`
> Junction table linking recipes to tags

| Column | Type | Description |
|--------|------|-------------|
| `recipeId` **PK** | `UUID` **NOT NULL** | Reference to recipe → `recipe.id` |
| `tagId` **PK** | `UUID` **NOT NULL** | Reference to tag → `tag.id` |


### `recipe_type`
> Categories or types of recipes

| Column | Type | Description |
|--------|------|-------------|
| `id` **PK** | `UUID` **NOT NULL** | Unique identifier (primary key) |
| `createdAt` | `TIMESTAMP` **NOT NULL** | Timestamp when record was created |
| `updatedAt` | `TIMESTAMP` **NOT NULL** | Timestamp when record was last updated |


### `recipe_type_translation`
> Translated names for recipe types

| Column | Type | Description |
|--------|------|-------------|
| `id` **PK** | `INTEGER` **NOT NULL** | Unique identifier (primary key) |
| `name` | `VARCHAR` | Translated name |
| `languageId` | `VARCHAR` | Reference to language → `language.id` |
| `recipeTypeId` | `UUID` | Reference to recipe type → `recipe_type.id` |


### `recipe_recipe_types_recipe_type`
> Junction table linking recipes to types

| Column | Type | Description |
|--------|------|-------------|
| `recipeId` **PK** | `UUID` **NOT NULL** | Reference to recipe → `recipe.id` |
| `recipeTypeId` **PK** | `UUID` **NOT NULL** | Reference to recipe type → `recipe_type.id` |


### `ingredient`
> Ingredient base information with name and language

| Column | Type | Description |
|--------|------|-------------|
| `id` **PK** | `UUID` **NOT NULL** | Unique identifier (primary key) |
| `name` | `VARCHAR` **NOT NULL** | Ingredient name |
| `createdAt` | `TIMESTAMP` **NOT NULL** | Timestamp when record was created |
| `updatedAt` | `TIMESTAMP` **NOT NULL** | Timestamp when record was last updated |
| `languageId` | `VARCHAR` | Reference to language → `language.id` |
| `embedding` | `VECTOR(1536)` | Vector embedding for semantic search |


### `ingredient_macros`
> Macronutrient information for ingredients (calories, protein, carbs, fats, etc.)

| Column | Type | Description |
|--------|------|-------------|
| `id` **PK** | `UUID` **NOT NULL** | Unique identifier (primary key) |
| `ingredientId` **FK** | `UUID` **NOT NULL** | Reference to ingredient → `ingredient.id` |
| `servingSize` | `INTEGER` **NOT NULL** | Serving size |
| `energyKcal` | `NUMERIC(10, 2)` | Energy in kcal |
| `energyKj` | `NUMERIC(10, 2)` | Energy in kJ |
| `protein` | `NUMERIC(10, 2)` | Protein |
| `carbohydrates` | `NUMERIC(10, 2)` | Carbohydrates |
| `totalFiber` | `NUMERIC(10, 2)` | Total fiber |
| `solubleFiber` | `NUMERIC(10, 2)` | Soluble fiber |
| `insolubleFiber` | `NUMERIC(10, 2)` | Insoluble fiber |
| `totalSugars` | `NUMERIC(10, 2)` | Total sugars |
| `addedSugar` | `NUMERIC(10, 2)` | Added sugar |
| `starch` | `NUMERIC(10, 2)` | Starch |
| `totalFat` | `NUMERIC(10, 2)` | Total fat |
| `saturatedFat` | `NUMERIC(10, 2)` | Saturated fat |
| `transFat` | `NUMERIC(10, 2)` | Trans fat |
| `monounsaturatedFat` | `NUMERIC(10, 2)` | Monounsaturated fat |
| `polyunsaturatedFat` | `NUMERIC(10, 2)` | Polyunsaturated fat |
| `cholesterol` | `NUMERIC(10, 2)` | Cholesterol |
| `createdAt` | `TIMESTAMP` **NOT NULL** | Timestamp when record was created |
| `updatedAt` | `TIMESTAMP` **NOT NULL** | Timestamp when record was last updated |


### `ingredient_micros`
> Micronutrient information for ingredients (vitamins and minerals)

| Column | Type | Description |
|--------|------|-------------|
| `id` **PK** | `UUID` **NOT NULL** | Unique identifier (primary key) |
| `ingredientId` **FK** | `UUID` **NOT NULL** | Reference to ingredient → `ingredient.id` |
| `servingSize` | `INTEGER` **NOT NULL** | Serving size |
| `vitaminA` | `NUMERIC(10, 2)` | Vitamin A |
| `vitaminC` | `NUMERIC(10, 2)` | Vitamin C |
| `vitaminD` | `NUMERIC(10, 2)` | Vitamin D |
| `vitaminE` | `NUMERIC(10, 2)` | Vitamin E |
| `vitaminK` | `NUMERIC(10, 2)` | Vitamin K |
| `thiamineB1` | `NUMERIC(10, 2)` | Thiamine B1 |
| `riboflavinB2` | `NUMERIC(10, 2)` | Riboflavin B2 |
| `niacinB3` | `NUMERIC(10, 2)` | Niacin B3 |
| `pantothenicAcidB5` | `NUMERIC(10, 2)` | Pantothenic acid B5 |
| `vitaminB6` | `NUMERIC(10, 2)` | Vitamin B6 |
| `biotinB7` | `NUMERIC(10, 2)` | Biotin B7 |
| `folateB9` | `NUMERIC(10, 2)` | Folate B9 |
| `vitaminB12` | `NUMERIC(10, 2)` | Vitamin B12 |
| `choline` | `NUMERIC(10, 2)` | Choline |
| `calcium` | `NUMERIC(10, 2)` | Calcium |
| `iron` | `NUMERIC(10, 2)` | Iron |
| `magnesium` | `NUMERIC(10, 2)` | Magnesium |
| `phosphorus` | `NUMERIC(10, 2)` | Phosphorus |
| `potassium` | `NUMERIC(10, 2)` | Potassium |
| `sodium` | `NUMERIC(10, 2)` | Sodium |
| `zinc` | `NUMERIC(10, 2)` | Zinc |
| `copper` | `NUMERIC(10, 2)` | Copper |
| `manganese` | `NUMERIC(10, 2)` | Manganese |
| `selenium` | `NUMERIC(10, 2)` | Selenium |
| `fluoride` | `NUMERIC(10, 2)` | Fluoride |
| `createdAt` | `TIMESTAMP` **NOT NULL** | Timestamp when record was created |
| `updatedAt` | `TIMESTAMP` **NOT NULL** | Timestamp when record was last updated |


### `ingredient_pricing`
> Pricing information for ingredients across different countries and currencies

| Column | Type | Description |
|--------|------|-------------|
| `id` **PK** | `UUID` **NOT NULL** | Unique identifier (primary key) |
| `ingredientId` **FK** | `UUID` **NOT NULL** | Reference to ingredient → `ingredient.id` |
| `countryId` **FK** | `UUID` **NOT NULL** | Reference to country → `country.id` |
| `currencyId` **FK** | `UUID` **NOT NULL** | Reference to currency → `currency.id` |
| `pricePerUnit` | `NUMERIC(10, 2)` **NOT NULL** | Price per single unit of measurement |
| `quantity` | `INTEGER` **NOT NULL** | Amount or count |
| `measuringUnitId` **FK** | `UUID` **NOT NULL** | Reference to measuring unit → `measuring_unit.id` |
| `createdAt` | `TIMESTAMP` **NOT NULL** | Timestamp when record was created |
| `updatedAt` | `TIMESTAMP` **NOT NULL** | Timestamp when record was last updated |


### `seasonality`
> Seasonality categories (weather, festivals, dietary practices, etc.)

| Column | Type | Description |
|--------|------|-------------|
| `id` **PK** | `UUID` **NOT NULL** | Unique identifier (primary key) |
| `type` | `VARCHAR(50)` **NOT NULL** | Type or category of seasonality |
| `isActive` | `BOOLEAN` **NOT NULL** | Whether the seasonality tag is currently active |
| `createdAt` | `TIMESTAMP` **NOT NULL** | Timestamp when record was created |
| `updatedAt` | `TIMESTAMP` **NOT NULL** | Timestamp when record was last updated |
| `embedding` | `VECTOR(1536)` | Vector embedding for semantic search |


### `seasonality_translation`
> Translated names for seasonality categories

| Column | Type | Description |
|--------|------|-------------|
| `id` **PK** | `INTEGER` **NOT NULL** | Unique identifier (primary key) |
| `name` | `VARCHAR(255)` **NOT NULL** | Translated name |
| `description` | `TEXT` | Text description |
| `languageId` | `VARCHAR(2)` **NOT NULL** | Reference to language → `language.id` |
| `seasonalityId` **FK** | `UUID` **NOT NULL** | Reference to seasonality → `seasonality.id` |
| `createdAt` | `TIMESTAMP` **NOT NULL** | Timestamp when record was created |
| `updatedAt` | `TIMESTAMP` **NOT NULL** | Timestamp when record was last updated |


### `tag`
> Tags for categorizing content

| Column | Type | Description |
|--------|------|-------------|
| `id` **PK** | `UUID` **NOT NULL** | Unique identifier (primary key) |
| `name` | `VARCHAR` **NOT NULL** | Tag name |
| `embedding` | `VECTOR(1536)` | Vector embedding for semantic search |


### `language`
> Language reference table with ISO codes

| Column | Type | Description |
|--------|------|-------------|
| `id` **PK** | `VARCHAR` **NOT NULL** | Language ID (ISO code) |
| `name` | `VARCHAR` **NOT NULL** | Language name |
| `globalName` | `VARCHAR` | Global language name |


### `country`
> Country reference table with codes and names

| Column | Type | Description |
|--------|------|-------------|
| `id` **PK** | `UUID` **NOT NULL** | Unique identifier (primary key) |
| `code` | `VARCHAR(3)` **NOT NULL** | ISO country code |
| `name` | `VARCHAR(100)` **NOT NULL** | Country name |
| `createdAt` | `TIMESTAMP` **NOT NULL** | Timestamp when record was created |
| `updatedAt` | `TIMESTAMP` **NOT NULL** | Timestamp when record was last updated |


### `currency`
> Currency reference table with codes and symbols

| Column | Type | Description |
|--------|------|-------------|
| `id` **PK** | `UUID` **NOT NULL** | Unique identifier (primary key) |
| `code` | `VARCHAR(3)` **NOT NULL** | ISO currency code |
| `name` | `VARCHAR(50)` **NOT NULL** | Currency name |
| `symbol` | `VARCHAR(10)` **NOT NULL** | Currency symbol |
| `createdAt` | `TIMESTAMP` **NOT NULL** | Timestamp when record was created |
| `updatedAt` | `TIMESTAMP` **NOT NULL** | Timestamp when record was last updated |


### `measuring_unit`
> Standard units of measurement (cups, grams, etc.)

| Column | Type | Description |
|--------|------|-------------|
| `id` **PK** | `UUID` **NOT NULL** | Unique identifier (primary key) |
| `createdAt` | `TIMESTAMP` **NOT NULL** | Timestamp when record was created |
| `updatedAt` | `TIMESTAMP` **NOT NULL** | Timestamp when record was last updated |


### `measuring_unit_translation`
> Translated names for measuring units

| Column | Type | Description |
|--------|------|-------------|
| `id` **PK** | `INTEGER` **NOT NULL** | Unique identifier (primary key) |
| `name` | `VARCHAR` | Translated name |
| `longName` | `VARCHAR` | Full/long form name |
| `languageId` | `VARCHAR` | Reference to language → `language.id` |
| `measuringUnitId` | `UUID` | Reference to measuring unit → `measuring_unit.id` |


### `user`
> User account information including profile details, role, and preferences

| Column | Type | Description |
|--------|------|-------------|
| `uid` **PK** | `VARCHAR` **NOT NULL** | Unique user identifier |
| `username` | `VARCHAR` | User's chosen username for login/display |
| `bio` | `VARCHAR` | User biography or profile description |
| `createdAt` | `TIMESTAMP` **NOT NULL** | Timestamp when record was created |
| `lastSeen` | `TIMESTAMP` | Last timestamp user was active |
| `name` | `VARCHAR` | User's display name |
| `image` | `VARCHAR` | URL/path to user's profile image |
| `dob` | `TIMESTAMP` | Date of birth |
| `gender` | `VARCHAR(6)` | User's gender (male, female, other) |
| `messagingTokens` | `TEXT` | Push notification tokens for sending messages |
| `termsAccepted` | `BOOLEAN` | Whether user has accepted terms of service |
| `role` | `VARCHAR(11)` **NOT NULL** | User role/permission level (admin, vip, team_sulten, verified, community) |
| `tag` | `VARCHAR` | User tag or label for categorization |


### `user_purchase`
> User purchase history for subscriptions and bundles

| Column | Type | Description |
|--------|------|-------------|
| `id` **PK** | `UUID` **NOT NULL** | Unique identifier (primary key) |
| `transactionId` | `VARCHAR` **NOT NULL** | Unique transaction identifier |
| `purchaseType` | `VARCHAR(3)` **NOT NULL** | Type of purchase (iap=in-app purchase, web) |
| `platform` | `VARCHAR(7)` **NOT NULL** | Purchase platform (ios, android, web) |
| `purchasedAt` | `TIMESTAMP` **NOT NULL** | Timestamp when purchase was made |
| `userUid` | `VARCHAR` | Reference to user → `user.uid` |
| `bundleId` | `UUID` | Reference to bundle → `bundle.id` |
| `purchasedAtMillis` | `BIGINT` | Purchase timestamp in milliseconds since epoch |


### `user_likes_recipe`
> User liked recipes tracking

| Column | Type | Description |
|--------|------|-------------|
| `userUid` **PK** | `VARCHAR` **NOT NULL** | Reference to user → `user.uid` |
| `recipeId` **PK** | `UUID` **NOT NULL** | Reference to recipe → `recipe.id` |


### `like`
> Generic like tracking for any entity type

| Column | Type | Description |
|--------|------|-------------|
| `id` **PK** | `UUID` **NOT NULL** | Unique identifier (primary key) |
| `entityId` | `VARCHAR` **NOT NULL** | ID of the entity being commented/liked |
| `entityType` | `VARCHAR` **NOT NULL** | Type of entity (recipe, video, etc.) |
| `createdAt` | `TIMESTAMP` **NOT NULL** | Timestamp when record was created |
| `userUid` | `VARCHAR` | Reference to user → `user.uid` |


### `bundle`
> Recipe bundles/packages available for purchase

| Column | Type | Description |
|--------|------|-------------|
| `id` **PK** | `UUID` **NOT NULL** | Unique identifier (primary key) |
| `createdAt` | `TIMESTAMP` **NOT NULL** | Timestamp when record was created |
| `updatedAt` | `TIMESTAMP` **NOT NULL** | Timestamp when record was last updated |
| `name` | `VARCHAR` **NOT NULL** | Bundle name |
| `ingress` | `TEXT` **NOT NULL** | Short summary or introduction text |
| `isActive` | `BOOLEAN` **NOT NULL** | Whether the bundle is currently active |
| `image` | `VARCHAR` | URL/path to bundle image |
| `userUid` | `VARCHAR` | Reference to user → `user.uid` |
| `pricingDetailId` | `UUID` | Reference to pricing details → `bundle_price.id` |
| `deletedAt` | `TIMESTAMP` | Soft delete timestamp (null if not deleted) |


### `bundle_price`
> Pricing details for bundles across platforms

| Column | Type | Description |
|--------|------|-------------|
| `id` **PK** | `UUID` **NOT NULL** | Unique identifier (primary key) |
| `price` | `INTEGER` **NOT NULL** | Price value |
| `iosName` | `VARCHAR` | Display name for iOS platform |
| `androidName` | `VARCHAR` | Display name for Android platform |
| `name` | `VARCHAR` | Migration name/identifier |


### `bundle_recipe`
> Recipes included in each bundle

| Column | Type | Description |
|--------|------|-------------|
| `bundleId` **PK** | `UUID` **NOT NULL** | Reference to bundle → `bundle.id` |
| `recipeId` **PK** | `UUID` **NOT NULL** | Reference to recipe → `recipe.id` |
| `order` | `INTEGER` **NOT NULL** | Sorting order for display |
| `isFree` | `BOOLEAN` **NOT NULL** | Whether the recipe is free |
| `deletedAt` | `TIMESTAMP` | Soft delete timestamp (null if not deleted) |


### `migrations`
> Database migration tracking

| Column | Type | Description |
|--------|------|-------------|
| `id` **PK** | `INTEGER` **NOT NULL** | Unique identifier (primary key) |
| `timestamp` | `BIGINT` **NOT NULL** | Migration timestamp |
| `name` | `VARCHAR` **NOT NULL** | Migration name/identifier |


## Relationships:
- `chat_message`.`session_id` → `chat_session`.`id`
- `recipe`.`languageId` → `language`.`id`
- `recipe`.`userUid` → `user`.`uid`
- `recipe_ingredient`.`recipeId` → `recipe`.`id`
- `recipe_ingredient`.`ingredientId` → `ingredient`.`id`
- `recipe_ingredient`.`unitId` → `measuring_unit`.`id`
- `recipe_instruction`.`recipeId` → `recipe`.`id`
- `recipe_instruction_selected_ingredients_ingredient`.`recipeInstructionId` → `recipe_instruction`.`id`
- `recipe_instruction_selected_ingredients_ingredient`.`ingredientId` → `ingredient`.`id`
- `recipe_seasonality`.`recipeId` → `recipe`.`id`
- `recipe_seasonality`.`seasonalityId` → `seasonality`.`id`
- `recipe_tags_tag`.`recipeId` → `recipe`.`id`
- `recipe_tags_tag`.`tagId` → `tag`.`id`
- `recipe_type_translation`.`recipeTypeId` → `recipe_type`.`id`
- `recipe_type_translation`.`languageId` → `language`.`id`
- `recipe_recipe_types_recipe_type`.`recipeId` → `recipe`.`id`
- `recipe_recipe_types_recipe_type`.`recipeTypeId` → `recipe_type`.`id`
- `ingredient`.`languageId` → `language`.`id`
- `ingredient_macros`.`ingredientId` → `ingredient`.`id`
- `ingredient_micros`.`ingredientId` → `ingredient`.`id`
- `ingredient_pricing`.`ingredientId` → `ingredient`.`id`
- `ingredient_pricing`.`countryId` → `country`.`id`
- `ingredient_pricing`.`currencyId` → `currency`.`id`
- `ingredient_pricing`.`measuringUnitId` → `measuring_unit`.`id`
- `seasonality_translation`.`languageId` → `language`.`id`
- `seasonality_translation`.`seasonalityId` → `seasonality`.`id`
- `measuring_unit_translation`.`languageId` → `language`.`id`
- `measuring_unit_translation`.`measuringUnitId` → `measuring_unit`.`id`
- `user_purchase`.`userUid` → `user`.`uid`
- `user_purchase`.`bundleId` → `bundle`.`id`
- `user_likes_recipe`.`userUid` → `user`.`uid`
- `user_likes_recipe`.`recipeId` → `recipe`.`id`
- `like`.`userUid` → `user`.`uid`
- `bundle`.`userUid` → `user`.`uid`
- `bundle`.`pricingDetailId` → `bundle_price`.`id`
- `bundle_recipe`.`bundleId` → `bundle`.`id`
- `bundle_recipe`.`recipeId` → `recipe`.`id`

## Vector Embeddings (Semantic Search)
The following tables have vector embeddings for semantic search using pgvector:
- `recipe`.`embedding` (VECTOR(1536)) - Includes recipe name, description, ingredients, tags, and seasonality
- `ingredient`.`embedding` (VECTOR(1536)) - Ingredient name
- `seasonality`.`embedding` (VECTOR(1536)) - Type, translations, and descriptions
- `tag`.`embedding` (VECTOR(1536)) - Tag name

Indexes:
- `idx_recipe_embedding_cosine` - Cosine similarity index on recipe.embedding
- `idx_ingredient_embedding_cosine` - Cosine similarity index on ingredient.embedding
- `idx_seasonality_embedding_cosine` - Cosine similarity index on seasonality.embedding
- `idx_tag_embedding_cosine` - Cosine similarity index on tag.embedding
