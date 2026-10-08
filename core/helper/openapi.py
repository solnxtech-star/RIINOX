def remove_uuid_format(result, generator, request, public):
    """
    Post-processing hook for drf-spectacular to remove the 'uuid' format
    from all parameters (especially path parameters) and schemas.
    This prevents Swagger UI from enforcing strict hyphenated Guid validation,
    allowing 32-character hex UUIDs to be passed natively.
    """
    # Remove format from path/query parameters
    for path, methods in result.get('paths', {}).items():
        for method, operation in methods.items():
            for param in operation.get('parameters', []):
                if param.get('schema', {}).get('format') == 'uuid':
                    del param['schema']['format']
    
    # Also remove format from components (response/request bodies)
    for schema_name, schema in result.get('components', {}).get('schemas', {}).items():
        for prop_name, prop in schema.get('properties', {}).items():
            if prop.get('format') == 'uuid':
                del prop['format']
                
    return result
