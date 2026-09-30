def test_create_get_search_item(client):
    created = client.post("/api/v1/items", json={"title": "test item xyz"})
    assert created.status_code == 201
    item_id = created.json()["id"]

    assert client.get(f"/api/v1/items/{item_id}").json()["title"] == "test item xyz"
    assert item_id in [
        i["id"] for i in client.get("/api/v1/items", params={"q": "item xyz"}).json()
    ]
    assert client.get("/api/v1/items/999999999").status_code == 404
