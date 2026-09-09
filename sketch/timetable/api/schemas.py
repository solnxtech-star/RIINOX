from drf_spectacular.utils import extend_schema

subject_schema = dict(
    list=extend_schema(
        summary="List subjects",
        description="Retrieve all active subjects for the current school.",
    ),
    retrieve=extend_schema(
        summary="Retrieve a subject",
        description="Get a single subject by ID.",
    ),
    create=extend_schema(
        summary="Create subject",
        description="Create a new subject. Allowed for Teachers and Admins only.",
    ),
    update=extend_schema(
        summary="Update subject",
        description="Full update of a subject. Teachers + Admins only.",
    ),
    partial_update=extend_schema(
        summary="Partial update subject",
        description="Partial update of a subject. Teachers + Admins only.",
    ),
    destroy=extend_schema(
        summary="Delete subject",
        description="Soft delete a subject. Admins only.",
    ),
)
