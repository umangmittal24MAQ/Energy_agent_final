import requests
r = requests.post("https://energyconsumptionreportingagent-appbe-cpghf9ewfmhpgwfn.westus-01.azurewebsites.net/api/trigger-scraper")
print(r.json())