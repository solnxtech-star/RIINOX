from django.urls import path, include
from rest_framework.routers import DefaultRouter
from core.applications.products.api.views import ProductViewSet

router = DefaultRouter()
router.register(r'products', ProductViewSet, basename='products')

app_name = 'products'

urlpatterns = [
    path('', include(router.urls)),
]
