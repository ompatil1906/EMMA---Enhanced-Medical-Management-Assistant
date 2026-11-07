import { NestFactory } from '@nestjs/core';
import { DocumentBuilder, SwaggerModule } from '@nestjs/swagger';
import { AppModule } from './app.module.js';
import { buildAppConfig } from './config/app-config.js';

const DOCS_PATH = 'docs';

interface AppBootstrapConfig {
  port: number;
  corsOrigins: string[];
}

/**
 * Bootstrap: Nest 11 application with Swagger/OpenAPI at /docs, CORS from
 * configuration, graceful shutdown hooks (closes the TypeORM DataSource) and
 * global pipe/filter/interceptor wiring in AppModule. The log surface is
 * deliberately secret-free: no URLs, no credentials.
 */
async function bootstrap(): Promise<void> {
  const app = await NestFactory.create(AppModule, { bufferLogs: false });

  const config = buildAppConfig();
  if (config.corsOrigins.length > 0) {
    const wildcardAllowed = config.corsOrigins.includes('*');
    app.enableCors({
      origin: wildcardAllowed ? true : config.corsOrigins,
      methods: ['GET', 'POST', 'PATCH', 'PUT', 'DELETE', 'OPTIONS'],
    });
  }

  app.enableShutdownHooks();

  const document = SwaggerModule.createDocument(
    app,
    new DocumentBuilder()
      .setTitle('EMMA Practice API')
      .setDescription(
        'Day-3 practice data backend: practices, calls, patient requests, handoffs and an append-only audit trail. '
          + 'SQLite for local development/tests, MySQL for prod/compose — schema is applied by migrations only, never synchronize.',
      )
      .setVersion('0.1.0')
      .addTag('Calls')
      .addTag('Requests')
      .addTag('Handoffs')
      .addTag('Audit Events')
      .addTag('Health')
      .build(),
  );
  SwaggerModule.setup(DOCS_PATH, app, document);

  await app.listen(config.port);
  console.log(`practice-api listening on :${config.port} (OpenAPI docs at /${DOCS_PATH})`);
}

bootstrap().catch((error: unknown) => {
  console.error('practice-api failed to start:', error instanceof Error ? error.message : error);
  process.exitCode = 1;
});
