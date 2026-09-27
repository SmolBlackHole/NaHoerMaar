<template>
	<div class="flex flex-col gap-6" aria-hidden="true">
		<div
			class="grid overflow-hidden rounded-xl border border-default sm:grid-cols-2 xl:grid-cols-4"
		>
			<div v-for="item in 4" :key="item" class="min-h-28 p-5">
				<USkeleton class="h-4 w-24" />
				<USkeleton class="mt-4 h-8 w-16" />
				<USkeleton class="mt-2 h-3 w-32" />
			</div>
		</div>
		<div class="grid gap-6 xl:grid-cols-2">
			<div v-for="panel in 2" :key="panel" class="rounded-xl bg-elevated/35 p-5">
				<USkeleton class="h-5 w-32" />
				<USkeleton class="mt-2 h-3 w-52" />
				<div class="mt-5 space-y-4">
					<div
						v-for="row in 4"
						:key="row"
						class="flex items-center justify-between gap-4"
					>
						<div class="flex-1">
							<USkeleton class="h-4" :class="row % 2 ? 'w-36' : 'w-52'" />
							<USkeleton class="mt-2 h-3 w-24" />
						</div>
						<USkeleton class="h-7 w-10 rounded-full" />
					</div>
				</div>
			</div>
		</div>
		<div class="grid gap-6 xl:grid-cols-2">
			<div v-for="panel in 2" :key="panel" class="rounded-xl bg-elevated/35 p-5">
				<USkeleton class="h-5 w-40" />
				<USkeleton class="mt-2 h-3 w-64" />
				<div class="mt-5 space-y-3">
					<USkeleton v-for="row in 3" :key="row" class="h-16 w-full rounded-lg" />
				</div>
			</div>
		</div>
	</div>
</template>
