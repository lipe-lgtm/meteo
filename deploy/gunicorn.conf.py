# Gunicorn si tento soubor načte sám (leží v pracovním adresáři služby).
# Ostatní nastavení je v deploy/meteo.service.

# Gunicorn 25+ zakládá řídicí socket v ~/.gunicorn – služba tam nesmí zapisovat
# a nepotřebuje ho. Starší verze tuto volbu tiše ignorují.
control_socket_disable = True
