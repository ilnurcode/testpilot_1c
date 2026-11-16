"""Минимальная публикация OData для ручной живой проверки чтения данных.

Это не учебная база 1С и не часть docker-compose: стенд поднимается на время проверки
внутри контейнера intelligence и гасится после прогона.

    docker compose exec -T intelligence python tests/fixtures/odata_stub.py

Слушает 127.0.0.1:9099 (ODATA_STUB_PORT): $metadata с одним EntitySet Catalog_Номенклатура
и коллекцию из двух записей. Любая другая сущность отвечает HTTP 404.
"""
import os

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response

EDMX = """<?xml version="1.0" encoding="utf-8"?>
<edmx:Edmx xmlns:edmx="http://schemas.microsoft.com/ado/2007/06/edmx" Version="3.0">
  <edmx:DataServices>
    <Schema xmlns="http://schemas.microsoft.com/ado/2009/11/edm" Namespace="StandardODATA">
      <EntityContainer Name="StandardODATA" m:IsDefaultEntityContainer="true"
                       xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata">
        <EntitySet Name="Catalog_Номенклатура" EntityType="StandardODATA.Catalog_Номенклатура"/>
      </EntityContainer>
    </Schema>
  </edmx:DataServices>
</edmx:Edmx>
"""

ITEMS = [
    {'Ref_Key': '11111111-1111-1111-1111-111111111111', 'Description': 'Товар 1', 'Code': '00001'},
    {'Ref_Key': '22222222-2222-2222-2222-222222222222', 'Description': 'Товар 2', 'Code': '00002'},
]

app = FastAPI(title='OData stub', docs_url=None, redoc_url=None, openapi_url=None)


@app.get('/odata/standard.odata/$metadata')
def metadata() -> Response:
    return Response(EDMX, media_type='application/xml')


@app.get('/odata/standard.odata/Catalog_Номенклатура')
def catalog() -> JSONResponse:
    return JSONResponse({'value': ITEMS})


if __name__ == '__main__':
    import uvicorn

    uvicorn.run(
        app,
        host=os.environ.get('ODATA_STUB_HOST', '127.0.0.1'),
        port=int(os.environ.get('ODATA_STUB_PORT', '9099')),
        log_level='warning',
    )
