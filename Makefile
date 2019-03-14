fetch:
	docker run -i \
		-e OUTPUT_PREFIX=/output \
		-v ${PWD}:/output \
		-v ${PWD}/general.yml:/app/general.yml \
		-t fixmystreet/confirm-asset-fetcher

build:
	docker -D build -t fixmystreet/confirm-asset-fetcher .

push: build
	docker push fixmystreet/confirm-asset-fetcher

shell:
	docker run -ti fixmystreet/confirm-asset-fetcher bash
