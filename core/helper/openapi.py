def remove_uuid_format(result, generator, request, public):
    """
    Post-processing hook for drf-spectacular to remove the 'uuid' format
    from all parameters (especially path parameters) and schemas.
    This prevents Swagger UI from enforcing strict hyphenated Guid validation,
    allowing 32-character hex UUIDs to be passed natively.
    """
    # Remove format from path/query parameters
    for path, methods in result.get('paths', {}).items():
        if not isinstance(methods, dict):
            continue
        for method, operation in methods.items():
            if not isinstance(operation, dict):
                continue
            for param in operation.get('parameters', []):
                if isinstance(param, dict) and param.get('schema', {}).get('format') == 'uuid':
                    del param['schema']['format']

    # Also remove format from components (response/request bodies)
    for schema_name, schema in result.get('components', {}).get('schemas', {}).items():
        if not isinstance(schema, dict):
            continue
        for prop_name, prop in schema.get('properties', {}).items():
            if isinstance(prop, dict) and prop.get('format') == 'uuid':
                del prop['format']

    return result
