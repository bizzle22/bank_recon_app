from django.urls import path

from .views import download_latest_report, home

urlpatterns = [

    path(
        "",
        home,
        name="home"
    ),

    path(
        "download-report/",
        download_latest_report,
        name="download_report"
    ),

]
