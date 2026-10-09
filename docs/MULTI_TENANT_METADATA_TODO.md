# TODO: Multi-Tenant Business-Specific Metadata Implementation

## Overview
As RIINOX expands to support multiple business types (Pharmacy, Retail, School, Hospitality, etc.), we will encounter the need to store data fields that are highly specific to certain industries (e.g., `dosage` for Pharmacy, `student_id` for Schools). 

To prevent our core database tables from becoming "Sparse Tables" (bloated with hundreds of nullable columns that only apply to a few tenants), we will adopt the **Metadata JSONField + Validation Registry Pattern**.

## The Implementation Plan

### 1. Model Updates
Instead of adding business-specific columns directly to core models, we will add a single `metadata` JSON field. This applies to entities like `Product`, `Customer`, `Sale`, and `Location`.

**Example:**
```python
# core/applications/products/models.py
from django.db import models

class Product(TimeBasedModel):
    # Core fields shared by everyone
    name = models.CharField(max_length=255)
    sku = models.CharField(max_length=100)
    
    # Business-specific data
    metadata = models.JSONField(
        default=dict, 
        blank=True,
        help_text="Stores unstructured data specific to a business type (e.g., dosage, shoe_size)"
    )
```

### 2. Validation Registry
To avoid a massive `if/else` block in our serializers, we will create a dedicated registry that defines the required and optional metadata fields for each business type.

**Example:**
```python
# core/applications/products/validators.py
BUSINESS_METADATA_RULES = {
    "pharmacy": {
        "required": ["dosage", "active_ingredient"],
        "optional": ["prescription_required"]
    },
    "fashion": {
        "required": ["size", "color"],
        "optional": ["material"]
    },
    "retail": {
        "required": [], 
        "optional": []
    }
}
```

### 3. Dynamic Serializer Validation
The serializers will read from the Validation Registry to dynamically enforce business rules based on the organization's `business_type`.

**Example:**
```python
# core/applications/products/api/serializers.py
from rest_framework import serializers
from .validators import BUSINESS_METADATA_RULES

class ProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = ['id', 'name', 'sku', 'metadata']

    def validate(self, attrs):
        # 1. Get the organization's business type
        request = self.context.get('request')
        business_type = request.user.organization.primary_business_type.code
        
        metadata = attrs.get('metadata', {})

        # 2. Look up the rules for this business type
        rules = BUSINESS_METADATA_RULES.get(business_type, {"required": [], "optional": []})

        # 3. Automatically validate required fields
        for required_field in rules["required"]:
            if required_field not in metadata:
                raise serializers.ValidationError({
                    "metadata": f"The '{required_field}' field is required for {business_type} records."
                })

        return attrs
```

### 4. Frontend Schema Discovery API
Because the backend `Validation Registry` holds the exact fields required for each business type, we should expose this dictionary via an API endpoint (e.g., `GET /api/business-types/metadata-schemas/`).

This prevents the frontend developers from having to guess or hardcode which fields apply to which business type. The frontend can simply fetch the schema, loop through the `required` and `optional` arrays, and dynamically render the exact input fields needed (achieving Server-Driven UI).

### Where This Should Be Applied
When this architecture is rolled out in the future, it should be applied to:
- **Products**: For industry-specific item details (e.g., dosage, sizes, materials).
- **Customers**: For industry-specific profile data (e.g., student IDs, passport numbers).
- **Sales / Invoices**: For transaction-specific context (e.g., room numbers, prescription images).
- **Locations**: For specialized location tracking (e.g., campuses vs. warehouses).
