from drf_spectacular.utils import (
    extend_schema,
    extend_schema_view,
    OpenApiParameter,
    OpenApiResponse,
    OpenApiExample,
)
from core.applications.accessments.api.serializers.accessment_entry_serializers import (
    AssessmentEntryFormDataSerializer,
    AssessmentRecordSerializer,
    BulkAssessmentEntrySerializer,
)
from core.applications.users.api.serializers.admin_serializers import (
    ClassRoomSerializer,
    ClassRoomCreateSerializer,
)
from core.applications.accessments.api.serializers.academic_serializers import (
    AcademicSessionSerializer,
    AcademicTermSerializer,
)
from core.applications.timetable.api.serializers import SubjectSerializer


UnifiedAcademicsSchema = extend_schema_view(
    # ------------------------------------
    # CLASSROOMS CRUD
    # ------------------------------------
    classrooms=extend_schema(
        tags=["Academics • Classrooms"],
        summary="List Classrooms",
        description="Retrieve all classrooms for the authenticated user's school.",
        responses=ClassRoomSerializer(many=True),
    ),
    create_classroom=extend_schema(
        tags=["Academics • Classrooms"],
        summary="Create Classroom",
        request=ClassRoomCreateSerializer,
        responses={201: ClassRoomSerializer},
    ),
    get_classroom=extend_schema(
        tags=["Academics • Classrooms"],
        summary="Retrieve Classroom",
        parameters=[
            OpenApiParameter(
                name="pk",
                type=str,
                location=OpenApiParameter.PATH,
                description="Classroom ID to retrieve",
            )
        ],
        responses={200: ClassRoomSerializer, 404: None},
    ),
    update_classroom=extend_schema(
        tags=["Academics • Classrooms"],
        summary="Update Classroom",
        request=ClassRoomCreateSerializer,
        responses={200: ClassRoomSerializer, 404: None},
    ),
    delete_classroom=extend_schema(
        tags=["Academics • Classrooms"],
        summary="Delete Classroom",
        responses={204: None, 404: None},
    ),
    # ------------------------------------
    # ACADEMIC SESSIONS
    # ------------------------------------
    list_sessions=extend_schema(
        tags=["Academics • Sessions"],
        summary="List Academic Sessions",
        responses=AcademicSessionSerializer(many=True),
    ),
    create_session=extend_schema(
        tags=["Academics • Sessions"],
        summary="Create Academic Session",
        request=AcademicSessionSerializer,
        responses={201: AcademicSessionSerializer},
    ),
    # ------------------------------------
    # ACADEMIC TERMS
    # ------------------------------------
    list_terms=extend_schema(
        tags=["Academics • Terms"],
        summary="List Academic Terms",
        parameters=[
            OpenApiParameter(
                name="session_id",
                type=str,
                required=True,
                location=OpenApiParameter.QUERY,
                description="Filter by academic session",
            )
        ],
        responses=AcademicTermSerializer(many=True),
    ),
    create_term=extend_schema(
        tags=["Academics • Terms"],
        summary="Create Academic Term",
        request=AcademicTermSerializer,
        responses={201: AcademicTermSerializer},
    ),
    # ------------------------------------
    # SUBJECTS
    # ------------------------------------
    list_subjects=extend_schema(
        tags=["Academics • Subjects"],
        summary="List Subjects",
        parameters=[
            OpenApiParameter(
                name="search",
                type=str,
                required=False,
                location=OpenApiParameter.QUERY,
                description="Filter subjects by name search",
            )
        ],
        responses=SubjectSerializer(many=True),
    ),
    create_subject=extend_schema(
        tags=["Academics • Subjects"],
        summary="Create Subject",
        request=SubjectSerializer,
        responses={201: SubjectSerializer},
    ),
)

accessment_entry_schema = extend_schema_view(
    list=extend_schema(
        summary="List all assessment records",
        description=(
            "Returns a paginated list of all assessment records belonging to "
            "the authenticated user's school.\n\n"
            "Includes student info, subject, assessment type, and computed percentage score."
        ),
        responses=AssessmentRecordSerializer,
    ),
    retrieve=extend_schema(
        summary="Retrieve a single assessment record",
        description=(
            "Returns all details about a specific assessment record, including:\n"
            "- Student information\n"
            "- Classroom & subject details\n"
            "- Assessment type\n"
            "- Score and percentage\n"
            "Access is restricted to the user's school."
        ),
        responses=AssessmentRecordSerializer,
    ),
    create=extend_schema(
        summary="Create a single assessment record",
        description=(
            "Create a single assessment score for a student.\n"
            "This endpoint is typically used for one-off entries.\n\n"
            "Validates:\n"
            "- Student must belong to the classroom\n"
            "- Score must not exceed max score\n"
            "- Index must not exceed assessment type allowed count"
        ),
        request=AssessmentRecordSerializer,
        responses={
            201: AssessmentRecordSerializer,
            400: OpenApiResponse(description="Validation error"),
        },
    ),
    update=extend_schema(
        summary="Update an assessment record",
        description=(
            "Updates an existing assessment record. "
            "Typically used to correct student scores.\n\n"
            "Permissions: Only users in the same school may update."
        ),
        request=AssessmentRecordSerializer,
        responses=AssessmentRecordSerializer,
    ),
    partial_update=extend_schema(
        summary="Partially update an assessment record",
        description="Update only specific fields of an assessment record.",
        request=AssessmentRecordSerializer,
        responses=AssessmentRecordSerializer,
    ),
    destroy=extend_schema(
        summary="Delete an assessment record",
        description=(
            "Deletes an assessment record completely.\n\n"
            "Only administrators or users with sufficient permissions should call this."
        ),
        responses={204: OpenApiResponse(description="Record deleted successfully")},
    ),
    # -------------------------------
    # FORM DATA ENDPOINT
    # -------------------------------
    form_data=extend_schema(
        summary="Retrieve assessment entry form data",
        description=(
            "Used by the frontend to load all required data for entering assessment scores.\n\n"
            "Request Body:\n"
            "- class_room_id\n"
            "- subject_id\n\n"
            "Responses include:\n"
            "- Classroom details\n"
            "- Subject details\n"
            "- Students in the classroom\n"
            "- Available assessment types\n"
            "- classroom_subject_id (required for score entry)"
        ),
        request=AssessmentEntryFormDataSerializer,
        responses={
            200: OpenApiResponse(
                description="Form data returned successfully",
                examples=[
                    OpenApiExample(
                        name="FormDataResponse",
                        value={
                            "classroom": {"id": 1, "name": "JSS 1A"},
                            "subject": {"id": 4, "name": "Mathematics"},
                            "students": [
                                {"id": 10, "name": "John Doe", "student_id": "STUD001"},
                                {
                                    "id": 11,
                                    "name": "Jane Smith",
                                    "student_id": "STUD002",
                                },
                            ],
                            "assessment_types": [
                                {"id": 2, "name": "Test", "max_score": 20, "count": 3},
                                {"id": 5, "name": "Exam", "max_score": 100, "count": 1},
                            ],
                            "classroom_subject_id": 14,
                        },
                    )
                ],
            ),
            400: OpenApiResponse(description="Invalid classroom/subject"),
        },
    ),
    # -------------------------------
    # BULK ENTRY ENDPOINT
    # -------------------------------
    bulk_entry=extend_schema(
        summary="Bulk enter assessment scores",
        description=(
            "Creates or updates assessment scores for multiple students at once.\n\n"
            "Useful for teachers entering an entire class score sheet.\n\n"
            "Validates:\n"
            "- All students must belong to the classroom\n"
            "- Index must not exceed allowed count for assessment type\n"
            "- Score must not exceed max score\n"
        ),
        request=BulkAssessmentEntrySerializer,
        responses={
            201: OpenApiResponse(
                description="Bulk records created successfully",
                examples=[
                    OpenApiExample(
                        name="BulkEntrySuccess",
                        value={
                            "message": "Successfully created 25 assessment records",
                            "count": 25,
                            "records": [
                                {
                                    "id": 41,
                                    "student": 10,
                                    "score": 15.0,
                                    "max_possible_score": 20,
                                    "percentage_score": 75.0,
                                },
                                {
                                    "id": 42,
                                    "student": 11,
                                    "score": 18.0,
                                    "max_possible_score": 20,
                                    "percentage_score": 90.0,
                                },
                            ],
                        },
                    )
                ],
            ),
            400: OpenApiResponse(description="Validation error in one or more entries"),
        },
    ),
)
