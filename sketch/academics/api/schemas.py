from drf_spectacular.utils import (
    extend_schema_view,
    extend_schema,
    OpenApiParameter,
    OpenApiExample,
    OpenApiResponse,
)

from core.applications.users.api.serializers.users_profile_serializers import (
    TeacherProfileSerializer,
)

from .serializers import (
    TeacherListSerializer,
    TeacherDetailSerializer,
    AdminAssignClassroomsSerializer,
    TeacherCreateTeachingAssignmentsSerializer,
    TeacherReassignTeachingAssignmentSerializer,
)


assigment_of_teachers_schema = extend_schema_view(

    # ---------------------------------------------------------
    # LIST TEACHERS
    # ---------------------------------------------------------
    list=extend_schema(
        summary="List all teachers",
        description=(
            "Retrieve a paginated list of teachers belonging to the "
            "authenticated user's school.\n\n"
            "🔒 Multi-tenant restricted.\n"
            "Supports pagination, filtering, and ordering."
        ),
        responses=TeacherListSerializer,
    ),

    # ---------------------------------------------------------
    # RETRIEVE TEACHER
    # ---------------------------------------------------------
    retrieve=extend_schema(
        summary="Retrieve a single teacher profile",
        description=(
            "Returns full teacher details including assigned classrooms.\n\n"
            "🔒 Only teachers within the authenticated user’s school "
            "may be accessed."
        ),
        responses=TeacherDetailSerializer,
    ),

    # ---------------------------------------------------------
    # ADMIN → ASSIGN CLASSROOMS
    # ---------------------------------------------------------
    assign_classrooms=extend_schema(
        summary="Assign multiple classrooms to a teacher (Admin only)",
        description=(
            "Allows **Principals** and **School Owners** to assign classrooms "
            "to a teacher.\n\n"
            "**Rules:**\n"
            "- Only admins may perform this action.\n"
            "- All classroom IDs must belong to the same school.\n"
            "- Replaces the current classrooms (`set()` behavior).\n"
        ),
        request=AdminAssignClassroomsSerializer,
        responses={
            200: OpenApiResponse(
                description="Classrooms successfully assigned.",
                response=TeacherDetailSerializer,
            ),
            400: OpenApiResponse(description="Validation error."),
            403: OpenApiResponse(description="Forbidden — Admin only."),
        },
        examples=[
            OpenApiExample(
                "Assign classrooms",
                value={
                    "classroom_ids": [
                        "58d68e24-fbc2-4e4c-aa8b-9fbb5f76a3ab",
                        "91afa932-e8a3-42b2-93bb-ac10739e9cd8",
                    ]
                },
            )
        ],
    ),

    # ---------------------------------------------------------
    # TEACHER → CREATE TEACHING ASSIGNMENTS
    # ---------------------------------------------------------
    assign_teaching=extend_schema(
        summary="Teacher assigns themselves to classrooms & subjects",
        description=(
            "Allows a teacher to assign **themselves** to multiple classroom + subject "
            "combinations.\n\n"
            "For example:\n"
            "```\n"
            "classroom_ids = [C1, C2]\n"
            "subject_ids   = [S1, S2]\n"
            "```\n"
            "Creates 4 assignments:\n"
            "- (C1, S1), (C1, S2), (C2, S1), (C2, S2)\n\n"
            "**Rules:**\n"
            "- Teachers can assign ONLY themselves.\n"
            "- All IDs must belong to the teacher's school.\n"
            "- Duplicate assignments are automatically prevented.\n"
        ),
        request=TeacherCreateTeachingAssignmentsSerializer,
        responses={
            200: OpenApiResponse(
                description="Teaching assignments created successfully.",
                response={
                    "type": "object",
                    "properties": {
                        "message": {"type": "string"},
                        "count": {"type": "integer"},
                        "assignments": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string", "format": "uuid"},
                                    "classroom": {"type": "string", "format": "uuid"},
                                    "subject": {"type": "string", "format": "uuid"},
                                },
                            },
                        },
                    },
                },
            ),
            400: OpenApiResponse(description="Validation error."),
            403: OpenApiResponse(description="Forbidden — Teachers can only assign themselves."),
        },
        examples=[
            OpenApiExample(
                "Assign teaching",
                value={
                    "classroom_ids": [
                        "c4a1b2f9-54fa-4e90-ac0d-14f7465adab7",
                        "1b2fbdb2-f0e1-4c28-9b72-0ed6124a14f1",
                    ],
                    "subject_ids": [
                        "e3c1de13-71d3-4b46-9e2a-f0e9878c9874",
                        "a49a9fbb-6a55-4c8f-ac17-52f8efb9e230",
                    ],
                },
            )
        ],
    ),

    # ---------------------------------------------------------
    # TEACHER → REASSIGN/UPDATE ONE ASSIGNMENT
    # ---------------------------------------------------------
    reassign_teaching=extend_schema(
        summary="Update a single teaching assignment (Teacher only)",
        description=(
            "Allows a teacher to **update an existing teaching assignment**, "
            "such as changing the classroom or subject.\n\n"
            "**Rules:**\n"
            "- Teacher may only update their own assignments.\n"
            "- No duplicate assignment allowed (unique constraint).\n"
            "- Classroom and subject must belong to the teacher’s school.\n"
        ),
        request=TeacherReassignTeachingAssignmentSerializer,
        responses={
            200: OpenApiResponse(
                description="Teaching assignment updated.",
                response={
                    "type": "object",
                    "properties": {
                        "message": {"type": "string"},
                        "assignment": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string", "format": "uuid"},
                                "classroom": {"type": "string", "format": "uuid"},
                                "subject": {"type": "string", "format": "uuid"},
                            },
                        },
                    },
                },
            ),
            400: OpenApiResponse(description="Validation error."),
            403: OpenApiResponse(description="Forbidden — cannot modify another teacher’s assignment."),
            404: OpenApiResponse(description="Assignment not found."),
        },
        examples=[
            OpenApiExample(
                "Reassign teaching",
                value={
                    "classroom_id": "e33bb4b3-055e-4b29-b7d9-98252a4e1c6f",
                    "subject_id": "ff52f235-1bf2-4edd-a258-e83dc3e28ef5",
                },
            )
        ],
    ),
)
