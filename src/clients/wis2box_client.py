import logging
import requests

log = logging.getLogger(__name__)


class WIS2BoxClient:
    def __init__(self, api_url, auth_token=None):
        self.api_url = api_url.rstrip("/")
        self.session = requests.Session()
        if auth_token:
            self.session.headers.update({"Authorization": f"Bearer {auth_token}"})

    def test_connection(self):
        try:
            r = self.session.get(f"{self.api_url}", timeout=10)
            r.raise_for_status()
            return True, r.json()
        except Exception as e:
            return False, str(e)

    def get_collections(self):
        try:
            r = self.session.get(f"{self.api_url}/collections", timeout=10)
            r.raise_for_status()
            data = r.json()
            return data.get("collections", [])
        except Exception as e:
            log.error(f"Failed to fetch collections: {e}")
            return []

    def get_collection_items(self, collection_id, limit=10, **params):
        params.setdefault("limit", limit)
        try:
            r = self.session.get(
                f"{self.api_url}/collections/{collection_id}/items",
                params=params, timeout=10
            )
            r.raise_for_status()
            return r.json()
        except Exception as e:
            log.error(f"Failed to fetch collection items: {e}")
            return None

    def get_discovery_metadata(self):
        return self.get_collection_items("discovery-metadata", limit=100)

    def validate_token(self, path="collections/stations"):
        try:
            r = self.session.get(
                f"{self.api_url}/{path}/items?limit=1", timeout=10
            )
            return r.status_code == 200, r.status_code
        except Exception as e:
            return False, str(e)

    def get_processes(self):
        try:
            r = self.session.get(f"{self.api_url}/processes", timeout=10)
            r.raise_for_status()
            data = r.json()
            return data.get("processes", [])
        except Exception as e:
            log.error(f"Failed to fetch processes: {e}")
            return []
