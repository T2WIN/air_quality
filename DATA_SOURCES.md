# OpenAQ endpoints

### 1. Get All Locations in France & Their Metadata: `GET /v3/locations`
This endpoint retrieves the monitoring stations in France along with their core metadata. 
* **`iso`** *(string, query)*: Use **`iso=FR`** to target France.
* **`limit`** *(integer, query)*: The number of results per request (Default: `100`).
* **`page`** *(integer, query)*: Increment this to paginate through all French stations.

**Metadata Included in the Response:**
For every location returned, the JSON payload will automatically include a wealth of metadata, such as:
* `name`: The name of the station.
* `coordinates`: The exact `latitude` and `longitude`.
* `timezone`: The local timezone (e.g., `Europe/Paris`).
* `isMobile` & `isMonitor`: Boolean values indicating the station type.
* `owner` & `provider`: Objects detailing who runs the station and provides the data.
* `bounds`: An array of 4 coordinates representing the geospatial bounding box.

*(Note: If you ever need to fetch this metadata for one specific station on demand rather than in bulk, you can use **`GET /v3/locations/{locations_id}`**).*

### 2. Get Sensors for All Pollutants: `GET /v3/locations/{locations_id}/sensors`
For each location ID retrieved in Step 1, use this endpoint to list all sensors attached to that station. 
* **`locations_id`** *(integer, path, required)*: The unique identifier for the monitoring location.
* **`limit`** *(integer, query)*: Limits the number of results returned (Default: `100`).
* **`page`** *(integer, query)*: Used to paginate through results.

*Keep a running list of every `sensors_id` extracted here to fetch the actual pollution data.*

### 3. Get Hourly Measurements: `GET /v3/sensors/{sensors_id}/hours`
Iterate through your collected `sensors_id` values to fetch the hourly aggregated data for every pollutant at every station in France.
* **`sensors_id`** *(integer, path, required)*: The unique ID of the specific sensor.
* **`datetime_from`** *(string, query)*: The start of your time range in ISO-8601 UTC format (e.g., `2024-01-01T00:00:00Z`).
* **`datetime_to`** *(string, query)*: The end of your time range in ISO-8601 UTC format.
* **`limit`** *(integer, query)*: Limits the number of results returned (Default: `100`).
* **`page`** *(integer, query)*: Used to paginate through the historical data.

---

### Key Reminders for this Workflow

* **Authentication:** Always include your API key in the headers (`X-API-Key: YOUR-OPENAQ-API-KEY`).
* **Rate Limiting:** Because querying an entire country requires fetching data for hundreds of locations and thousands of sensors, your script should handle pagination carefully and implement pauses to avoid hitting API rate limits or `408 Request Timeout` errors.
* **Time Formatting:** OpenAQ uses an **exclusive time-ending standard** (a `03:00` measurement is the average of data from `02:00` to `02:59`). Always query using UTC (`Z`), though your metadata from Step 1 will help you convert back to local French time if needed.

### Stations info
Number of French stations : 828
Number of sensors for french stations : 2208
38 stations have a sensor that measures all of {"pm25", "pm10", "no2", "o3", "so2"}
207 measure all of {"pm25", "pm10", "no2", "o3"}
+----------------+---------------+
| parameter_name | station_count |
+----------------+---------------+
| no2            |           619 |
| o3             |           482 |
| pm10           |           560 |
| pm25           |           374 |
| so2            |           173 |
+----------------+---------------+

Stations seem to report every 4h. When they report, they seem to report every hour average since the last report. So a station reported at 4pm and now reports at 8pm, for now (only quick checks), it seems like it report 5pm values, 6pm, 7pm and 8pm. Note that data on OpenAQ are harmonized to follow an exclusive time-ending standard. This means a time stamp for an hourly measurement 03:00 represents the data from 02:00 until 02:59.