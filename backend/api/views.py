import os

from rest_framework.views import APIView
from rest_framework.response import Response

from reconciliation.services.excel_reader import ExcelReader
from reconciliation.services.matching_engine import MatchingEngine


class ReconciliationAPIView(APIView):

    def post(self, request):

        return Response({
            "status": "success"
        })