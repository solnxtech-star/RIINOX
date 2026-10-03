import cloudinary.uploader
from cloudinary_storage.storage import MediaCloudinaryStorage


class RawCloudinaryStorage(MediaCloudinaryStorage):
    """
    Cloudinary storage for raw files such as HTML, DOCX, PDF, etc.
    """

    def _upload(self, name, content):
        options = {
            "resource_type": "raw",
            "public_id": name,
        }

        return cloudinary.uploader.upload(
            content,
            **options,
        )
