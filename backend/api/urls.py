from django.urls import path

from .views import ReconciliationAPIView

urlpatterns = [

    path(
        "reconcile/",
        ReconciliationAPIView.as_view(),
        name="reconcile"
    ),

]