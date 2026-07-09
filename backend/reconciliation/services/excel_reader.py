import pandas as pd


class ExcelReader:

    @staticmethod
    def read(file_path):

        df = pd.read_excel(file_path)

        df.columns = df.columns.astype(str).str.strip()

        return df