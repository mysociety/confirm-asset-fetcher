fetch:
	docker run -i \
		-e OUTPUT_PREFIX=/output \
		-v ${PWD}:/output \
		-v ${PWD}/general.yml:/app/general.yml \
		-t fixmystreet/confirm-asset-fetcher

build:
	date > version.txt
	git rev-parse HEAD >> version.txt
	docker build -t fixmystreet/confirm-asset-fetcher .
	rm version.txt

publish:
	date > version.txt
	git rev-parse HEAD >> version.txt
	docker buildx build --push --platform linux/arm64/v8,linux/amd64 -t fixmystreet/confirm-asset-fetcher:latest .
	rm version.txt

shell:
	docker run -ti fixmystreet/confirm-asset-fetcher bash
